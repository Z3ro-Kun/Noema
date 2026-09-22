"""Does the recommender behave the way Noema says it does?

A semantic invariant suite, not a quality benchmark. Nothing here asserts
that a particular work is the right answer for a particular reader: there is
no ground truth for that on this corpus, so there is no precision@k and no
recall@k either. Inventing a "correct" top result would turn the suite into a
record of somebody's taste.

What it does assert is that the chain holds under controlled evidence:

    a rating            replayed through the real library service
        -> an established preference, from the real preference engine
        -> concepts, from the real vocabulary
        -> candidates, from the real corpus
        -> an explanation that names the preference that selected them

Seven profiles exercise different shapes of that chain -- concentrated,
literary, anime-heavy, cross-medium, two-directional, sparse, and one holding
an established combination. Each is built by replaying ratings through
`library_service`, so the preference engine does the same work it does for a
real reader, and a case that yields nothing is telling us something true.

The twelve invariants in this file are asserted for *every* case that reaches
a personalized state, as parametrized tests, rather than once against a
favourable one.

HTTP-level isolation -- 401 for anonymous, and no `user_id` parameter
redirecting a shelf -- is exercised against a live app in
`test_recommendations.py`. What this file adds on that front is the
service-level version over real profiles: two readers with different
histories, and the confirmation that only the authenticated id selects
anything.
"""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Concept, UserContentInteraction, Work, WorkConcept
from app.schemas.recommendation import RecommendationRead, RecommendationReasonRead
from app.services import product_service
from app.services.preference.dashboard import TasteDashboard, build_taste_dashboard
from app.services.preference.evidence import DIRECTION_NEGATIVE, DIRECTION_POSITIVE
from app.services.recommendation import (
    DEFAULT_DIVERSITY,
    DiversityRule,
    STATE_BUILDING,
    STATE_PERSONALIZED,
    apply_diversity,
    build_recommendations,
)
from tests.evaluation.builder import MissingCorpusWorkError
from tests.evaluation.recommendation_builder import build_recommendation_cases
from tests.evaluation.recommendation_dataset import RECOMMENDATION_CASES

# Every case that is expected to produce a shelf. F is the sparse one and has
# its own test.
PERSONALIZED_CASES = ("A", "B", "C", "D", "E", "G")

# Wide enough that membership questions are about selection rather than about
# where a work landed among forty.
WHOLE_RANKING = 50


@pytest.fixture(scope="module")
def case_letters() -> tuple[str, ...]:
    return tuple(spec.case for spec in RECOMMENDATION_CASES)


@pytest.fixture
async def cases(db_session: AsyncSession):
    """Every profile, built and rolled back with the test."""
    try:
        return await build_recommendation_cases(db_session)
    except MissingCorpusWorkError as exc:
        pytest.skip(f"corpus not ingested: {exc}")


async def shelf_for(session: AsyncSession, cases, case: str, limit: int = WHOLE_RANKING):
    built = cases[case]
    return built, await build_recommendations(session, built.user_id, limit=limit)


async def dashboard_for(session: AsyncSession, cases, case: str) -> TasteDashboard:
    return await build_taste_dashboard(session, cases[case].user_id)


async def concepts_of(session: AsyncSession, work_id: uuid.UUID) -> set[str]:
    rows = await session.execute(
        select(Concept.slug)
        .join(WorkConcept, WorkConcept.concept_id == Concept.id)
        .where(WorkConcept.work_id == work_id)
    )
    return set(rows.scalars().all())


# --- the cases produce what they were built to produce ---------------------


@pytest.mark.parametrize("case", PERSONALIZED_CASES)
async def test_each_profile_reaches_a_personalized_state(
    db_session: AsyncSession, cases, case: str
) -> None:
    """The precondition for every invariant below.

    A case that stopped producing established evidence would make the rest of
    this file vacuously green, so it is asserted rather than assumed.
    """
    _, result = await shelf_for(db_session, cases, case)

    assert result.state == STATE_PERSONALIZED
    assert result.recommendations
    assert result.established_preferences >= 1


# --- 1. candidates are not already in the reader's history -----------------


@pytest.mark.parametrize("case", PERSONALIZED_CASES)
async def test_nothing_the_reader_has_already_met_is_recommended(
    db_session: AsyncSession, cases, case: str
) -> None:
    built, result = await shelf_for(db_session, cases, case)

    recommended = {item.work_id for item in result.ranked}
    assert recommended, "the case produced candidates"
    assert not (recommended & built.rated_work_ids)

    # Asserted against the table too, not only against the fixture's own list.
    interacted = set(
        (
            await db_session.execute(
                select(UserContentInteraction.work_id).where(
                    UserContentInteraction.user_id == built.user_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert not (recommended & interacted)


# --- 2. every reason is a real work_concepts row ---------------------------


@pytest.mark.parametrize("case", PERSONALIZED_CASES)
async def test_every_reason_names_a_concept_the_work_carries(
    db_session: AsyncSession, cases, case: str
) -> None:
    """The failure the whole design exists to prevent: a fabricated reason."""
    _, result = await shelf_for(db_session, cases, case)

    for item in result.ranked:
        carried = await concepts_of(db_session, item.work_id)
        for reason in (*item.reasons, *item.cautions):
            for concept in reason.concepts:
                assert concept.key in carried, (
                    f"{item.title} was explained by {concept.key!r}, "
                    "which it does not carry"
                )


# --- 3 & 4. every reason is an established preference, on the right side ---


@pytest.mark.parametrize("case", PERSONALIZED_CASES)
async def test_every_positive_reason_is_an_established_positive_preference(
    db_session: AsyncSession, cases, case: str
) -> None:
    dashboard = await dashboard_for(db_session, cases, case)
    _, result = await shelf_for(db_session, cases, case)

    established = {
        item.key: item
        for item in dashboard.established_items()
        if item.direction == DIRECTION_POSITIVE
    }
    assert established, "the case has something positive to say"

    for item in result.ranked:
        for reason in item.reasons:
            key = "+".join(concept.key for concept in reason.concepts)
            assert key in established, f"{key!r} is not an established positive"
            assert reason.direction == DIRECTION_POSITIVE
            # The band travels with the preference rather than being recomputed.
            assert reason.confidence_band == established[key].confidence_band
            assert reason.rated_works == established[key].evidence.works_rated


@pytest.mark.parametrize("case", PERSONALIZED_CASES)
async def test_every_caution_is_an_established_negative_preference(
    db_session: AsyncSession, cases, case: str
) -> None:
    dashboard = await dashboard_for(db_session, cases, case)
    _, result = await shelf_for(db_session, cases, case)

    negatives = {
        item.key
        for item in dashboard.established_items()
        if item.direction == DIRECTION_NEGATIVE
    }

    for item in result.ranked:
        for caution in item.cautions:
            key = "+".join(concept.key for concept in caution.concepts)
            assert key in negatives, f"{key!r} is not an established negative"
            assert caution.direction == DIRECTION_NEGATIVE


# --- 5. emerging preferences never speak ----------------------------------


@pytest.mark.parametrize("case", PERSONALIZED_CASES)
async def test_an_emerging_preference_never_becomes_a_reason(
    db_session: AsyncSession, cases, case: str
) -> None:
    """Two ratings is discovery, not a settled preference.

    The engine's own established/emerging boundary decides, and nothing in
    the recommender may read across it.
    """
    dashboard = await dashboard_for(db_session, cases, case)
    _, result = await shelf_for(db_session, cases, case)

    emerging = {item.key for item in dashboard.emerging}
    established = {item.key for item in dashboard.established_items()}

    spoken = {
        "+".join(concept.key for concept in reason.concepts)
        for item in result.ranked
        for reason in (*item.reasons, *item.cautions)
    }

    assert not (spoken & (emerging - established))


# --- 6. repeated associations cannot inflate a contribution ---------------


@pytest.mark.parametrize("case", PERSONALIZED_CASES)
async def test_a_concept_is_never_counted_twice_for_one_work(
    db_session: AsyncSession, cases, case: str
) -> None:
    """Two guarantees at once, checked where they actually have to hold.

    The table permits one row per (work, concept), and the scorer accepts one
    contribution per canonical concept however many patterns name it -- so a
    work's reasons can never repeat a concept.
    """
    _, result = await shelf_for(db_session, cases, case)

    for item in result.ranked:
        keys = [
            concept.key
            for reason in (*item.reasons, *item.cautions)
            for concept in reason.concepts
        ]
        assert len(keys) == len(set(keys)), f"{item.title} counted a concept twice"

    duplicates = (
        await db_session.execute(
            select(func.count())
            .select_from(
                select(WorkConcept.work_id, WorkConcept.concept_id)
                .group_by(WorkConcept.work_id, WorkConcept.concept_id)
                .having(func.count() > 1)
                .subquery()
            )
        )
    ).scalar_one()
    assert duplicates == 0


# --- 7. a combination needs all of its constituents -----------------------


async def test_a_combination_only_explains_a_work_carrying_both_concepts(
    db_session: AsyncSession, cases
) -> None:
    """Case G's reason for existing.

    "Crime and Investigation + Urban Modernity" is the one pair this corpus
    reliably establishes. A work carrying only one of the two must never
    claim it -- the pair was admitted precisely because it is more selective
    than either part.
    """
    dashboard = await dashboard_for(db_session, cases, "G")
    _, result = await shelf_for(db_session, cases, "G")

    combinations = [
        item
        for item in dashboard.established_items()
        if len(item.features) > 1
    ]
    assert combinations, "case G establishes a combination"

    explained_by_pair = 0
    for item in result.ranked:
        carried = await concepts_of(db_session, item.work_id)
        for reason in (*item.reasons, *item.cautions):
            if len(reason.concepts) < 2:
                continue
            explained_by_pair += 1
            keys = {concept.key for concept in reason.concepts}
            assert keys <= carried, (
                f"{item.title} claimed {sorted(keys)} while carrying "
                f"{sorted(keys - carried)} of them not at all"
            )

    assert explained_by_pair, "the combination actually explained something"


async def test_a_work_carrying_one_constituent_gets_only_the_single_reason(
    db_session: AsyncSession, cases
) -> None:
    """The other half of the same rule, stated as a search for a counterexample."""
    dashboard = await dashboard_for(db_session, cases, "G")
    pairs = [
        {feature.key for feature in item.features}
        for item in dashboard.established_items()
        if len(item.features) > 1
    ]
    _, result = await shelf_for(db_session, cases, "G")

    partial = 0
    for item in result.ranked:
        carried = await concepts_of(db_session, item.work_id)
        claimed = [
            {concept.key for concept in reason.concepts}
            for reason in item.reasons
            if len(reason.concepts) > 1
        ]
        for pair in pairs:
            if pair & carried and not pair <= carried:
                partial += 1
                assert pair not in claimed

    assert partial, "some candidate carries one constituent without the other"


# --- 8. determinism -------------------------------------------------------


@pytest.mark.parametrize("case", PERSONALIZED_CASES)
async def test_the_same_profile_produces_the_same_shelf_every_time(
    db_session: AsyncSession, cases, case: str
) -> None:
    built = cases[case]

    runs = [
        [
            (item.work_id, round(item.score, 9))
            for item in (
                await build_recommendations(db_session, built.user_id, limit=12)
            ).recommendations
        ]
        for _ in range(3)
    ]

    assert runs[0] == runs[1] == runs[2]
    assert runs[0]


# --- 9. the diversity cap ------------------------------------------------


@pytest.mark.parametrize("case", PERSONALIZED_CASES)
async def test_the_diversity_caps_hold_while_other_candidates_remain(
    db_session: AsyncSession, cases, case: str
) -> None:
    """The caps bind at the front of the shelf and relax rather than truncate.

    Stated as two facts: the shelf is filled to the limit when candidates
    exist, and the leading portion respects the per-concept cap whenever some
    other concept had a candidate to offer.
    """
    built, full = await shelf_for(db_session, cases, case)
    limit = 6
    result = await build_recommendations(db_session, built.user_id, limit=limit)

    assert len(result.recommendations) == min(limit, len(full.ranked))

    leading = [item.leading_concept_key for item in result.recommendations]
    distinct_available = {item.leading_concept_key for item in full.ranked}
    if len(distinct_available) > 1:
        first = leading[: DEFAULT_DIVERSITY.max_per_leading_concept + 1]
        assert len(set(first)) > 1, (
            "other concepts had candidates, so the front of the shelf should "
            f"not be {first}"
        )


async def test_no_medium_is_dropped_because_of_its_medium(
    db_session: AsyncSession, cases
) -> None:
    """Case D exists for this: evidence that genuinely spans three media.

    The honest invariant is narrower than "every medium appears". On case D
    the two literature candidates rank nineteenth and twentieth of thirty-
    seven, so a shelf of twelve does not reach them -- and should not. What
    must never happen is a medium being removed *for being that medium*.

    So an absent medium has to fail one of two ways, both of them
    medium-blind: its best candidate ranked below the shelf, or the
    per-concept spacing deferred it -- which is shown by relaxing that cap
    alone, leaving the domain cap where it is, and watching it reappear.
    """
    limit = 12
    _, result = await shelf_for(db_session, cases, "D", limit=limit)

    available = {item.domain_slug for item in result.ranked}
    shown = {item.domain_slug for item in result.recommendations}
    assert len(available) > 1
    assert len(shown) > 1, "recommendations cross media"

    for slug in available - shown:
        best_rank = min(
            index
            for index, item in enumerate(result.ranked, start=1)
            if item.domain_slug == slug
        )
        if best_rank > limit:
            # Out-ranked, not filtered. The domain cap never even applied:
            # nothing of this medium reached the shelf to count against it.
            assert (
                sum(1 for item in result.recommendations if item.domain_slug == slug)
                < DEFAULT_DIVERSITY.max_per_domain(limit)
            )
            continue

        relaxed = apply_diversity(
            result.ranked,
            limit=limit,
            rule=DiversityRule(max_per_leading_concept=len(result.ranked)),
        )
        assert slug in {item.domain_slug for item in relaxed}, (
            f"{slug} ranked inside the shelf and stayed absent with the "
            "concept cap relaxed, so the domain cap removed it"
        )


async def test_the_domain_cap_is_what_spaces_a_single_medium_shelf(
    db_session: AsyncSession, cases
) -> None:
    """Case C: the evidence is anime-shaped, and manga still gets seats.

    The cap is the only reason a shelf built from one medium's evidence is
    not one medium deep, so it is asserted where it actually bites.
    """
    _, result = await shelf_for(db_session, cases, "C", limit=6)

    counts: dict[str, int] = {}
    for item in result.recommendations:
        counts[item.domain_slug] = counts.get(item.domain_slug, 0) + 1

    assert len(counts) > 1
    for slug, count in counts.items():
        assert count <= DEFAULT_DIVERSITY.max_per_domain(6) or len(
            [item for item in result.ranked if item.domain_slug != slug]
        ) == 0


async def test_every_medium_with_a_candidate_appears_on_a_long_enough_shelf(
    db_session: AsyncSession, cases
) -> None:
    """The other side: nothing is filtered out permanently."""
    _, result = await shelf_for(db_session, cases, "D", limit=WHOLE_RANKING)

    assert {item.domain_slug for item in result.recommendations} == {
        item.domain_slug for item in result.ranked
    }


# --- 10. the API projection says what the evidence says -------------------


@pytest.mark.parametrize("case", PERSONALIZED_CASES)
async def test_the_public_projection_matches_the_underlying_evidence(
    db_session: AsyncSession, cases, case: str
) -> None:
    """The DTO is where an explanation could quietly drift from its evidence.

    Built through the same product projection the endpoint uses, so what is
    checked is the object a client actually receives rather than a hand-made
    stand-in that could omit the drift.
    """
    dashboard = await dashboard_for(db_session, cases, case)
    built, result = await shelf_for(db_session, cases, case)
    established = {item.key: item for item in dashboard.established_items()}

    works = list(
        (
            await db_session.execute(
                select(Work)
                .options(selectinload(Work.domain))
                .where(Work.id.in_([item.work_id for item in result.recommendations]))
            )
        )
        .scalars()
        .all()
    )
    presentations = {
        presentation.work.id: presentation
        for presentation in await product_service.presentations_for(
            db_session, works, user_id=built.user_id
        )
    }

    for item in result.recommendations:
        presentation = presentations[item.work_id]
        projected = RecommendationRead(
            work=presentation.work,
            user_state=presentation.user_state,
            reasons=[
                RecommendationReasonRead.model_validate(reason)
                for reason in item.reasons
            ],
            cautions=[
                RecommendationReasonRead.model_validate(caution)
                for caution in item.cautions
            ],
            confidence_band=item.confidence_band,
        )

        carried = await concepts_of(db_session, item.work_id)
        for reason in (*projected.reasons, *projected.cautions):
            key = "+".join(concept.key for concept in reason.concepts)
            assert key in established
            assert {concept.key for concept in reason.concepts} <= carried
            assert reason.confidence_band == established[key].confidence_band
            assert reason.direction == established[key].direction

        # The leading reason decides the band a reader is shown.
        assert projected.confidence_band == projected.reasons[0].confidence_band

        # Nothing internal is declared on the DTO at all.
        dumped = projected.model_dump()
        for internal in ("score", "support", "penalty", "preference_evidence"):
            assert internal not in dumped

        # The reader never appears on their own shelf, projection included.
        assert projected.work.id not in built.rated_work_ids


# --- 11 & 12. one reader's evidence, and only theirs ----------------------


async def test_two_profiles_produce_shelves_from_their_own_evidence(
    db_session: AsyncSession, cases
) -> None:
    crime = await shelf_for(db_session, cases, "E")
    science = await shelf_for(db_session, cases, "D")

    crime_ids = [item.work_id for item in crime[1].recommendations]
    science_ids = [item.work_id for item in science[1].recommendations]

    assert crime_ids != science_ids
    # Each reader's own rated works are absent from their own shelf and may
    # perfectly well appear on the other's -- which is what "their own
    # evidence" means.
    assert not (set(crime_ids) & cases["E"].rated_work_ids)
    assert not (set(science_ids) & cases["D"].rated_work_ids)


async def test_only_the_supplied_user_id_selects_anything(
    db_session: AsyncSession, cases
) -> None:
    """The service reads one id, and the route takes it from the session.

    Asserted here by construction: asking for case A's shelf with case C's id
    returns case C's shelf, so there is no second channel by which a reader
    could be chosen.
    """
    _, mine = await shelf_for(db_session, cases, "A")
    _, theirs = await shelf_for(db_session, cases, "C")
    again = await build_recommendations(
        db_session, cases["C"].user_id, limit=WHOLE_RANKING
    )

    assert [item.work_id for item in again.recommendations] == [
        item.work_id for item in theirs.recommendations
    ]
    assert [item.work_id for item in mine.recommendations] != [
        item.work_id for item in theirs.recommendations
    ]


async def test_an_unknown_reader_gets_the_no_activity_state(
    db_session: AsyncSession, cases
) -> None:
    result = await build_recommendations(db_session, uuid.uuid4())

    assert result.state == "no_activity"
    assert result.recommendations == []


# --- the sparse case ------------------------------------------------------


async def test_a_sparse_profile_is_answered_honestly(
    db_session: AsyncSession, cases
) -> None:
    """One rating is not a taste, and nothing is invented to cover for that."""
    _, result = await shelf_for(db_session, cases, "F")

    assert result.state == STATE_BUILDING
    assert result.recommendations == []
    assert result.ranked == []
    assert result.candidates_considered == 0


async def test_no_popularity_fallback_appears_for_a_sparse_profile(
    db_session: AsyncSession, cases
) -> None:
    """There is no popularity model to fall back to, and this proves it.

    A recommender with a hidden global ranking would answer a sparse profile
    with the corpus' most-tagged works rather than with nothing.
    """
    _, sparse = await shelf_for(db_session, cases, "F")
    _, established = await shelf_for(db_session, cases, "A")

    assert sparse.recommendations == []
    assert established.recommendations, "the comparison is meaningful"


# --- what each case was built to show ------------------------------------


async def test_a_literature_profile_can_surface_literature(
    db_session: AsyncSession, cases
) -> None:
    """Case B. The medium is never a term in the score.

    Literature works carry far fewer concepts than anime or manga on this
    corpus, which is a tagging asymmetry rather than a rule -- so what must
    hold is that a literature candidate matching as many preferences as any
    other scores as well as any other.
    """
    _, result = await shelf_for(db_session, cases, "B")

    literature = [item for item in result.ranked if item.domain_slug == "literature"]
    assert literature, "a literature candidate is in the ranking"

    best_overall = max(item.score for item in result.ranked)
    assert literature[0].score == pytest.approx(best_overall), (
        "a literature work matching the same preference scores the same"
    )


async def test_a_recommendation_needs_no_text_or_embedding(
    db_session: AsyncSession, cases
) -> None:
    """Concepts are the mechanism, so metadata-only works are recommendable."""
    from app.models import ContentUnit, Embedding

    _, result = await shelf_for(db_session, cases, "A")

    textless = []
    for item in result.ranked:
        units = (
            await db_session.execute(
                select(func.count())
                .select_from(ContentUnit)
                .outerjoin(
                    Embedding,
                    (Embedding.owner_id == ContentUnit.id)
                    & (Embedding.owner_type == "content_unit"),
                )
                .where(ContentUnit.work_id == item.work_id)
            )
        ).scalar_one()
        if units == 0:
            textless.append(item)

    assert textless, "the ranking includes works with no work-level text at all"


async def test_positive_and_negative_evidence_both_act(
    db_session: AsyncSession, cases
) -> None:
    """Case E, stated as the three things the rule promises."""
    dashboard = await dashboard_for(db_session, cases, "E")
    _, result = await shelf_for(db_session, cases, "E")

    directions = {item.direction for item in dashboard.established_items()}
    assert directions == {DIRECTION_POSITIVE, DIRECTION_NEGATIVE}

    with_caution = [item for item in result.ranked if item.cautions]
    without = [item for item in result.ranked if not item.cautions]
    assert with_caution and without

    # Supported and penalised ranks below supported alone...
    assert min(item.score for item in without) > max(
        item.score for item in with_caution
    )
    # ...and every scored candidate has at least one positive reason, so a
    # work matched only by a dislike is not on the list at all.
    assert all(item.reasons for item in result.ranked)
    assert result.candidates_matched < result.candidates_considered
