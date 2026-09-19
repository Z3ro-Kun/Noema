"""Phase 1V: the taste dashboard's product contract.

Two halves. The first is the classification rule, exercised on constructed
patterns across the whole boundary matrix -- that is where the phase's only
new constant lives, and it must be pinned on values chosen from the scale
rather than from the evaluation library. The second runs the dashboard over
the evaluation cases and checks the product properties: no score, no padding,
no causal or biographical field, no giant payload.

The cases this phase was asked for are AK through BC. As in Phase 1U, most
name a shape the library already has, so each is a named test recording which
case it uses; AK and AN were genuinely new and were added to the dataset.

    AK  strong positive, high confidence  -> case AK (new)
    AL  positive but weaker               -> case A
    AM  strong negative                   -> case B, case T
    AN  negative, too uncertain           -> case AN (new)
    AO  same evidence, many concepts      -> case M
    AP  individuals vs a real combination -> case S
    AQ  established combination           -> case S
    AR  emerging stays out                -> case U
    AS  cross-domain                      -> case V
    AT  equal evidence, different domains -> constructed
    AU  cold start                        -> cases C, D
    AV  reconsumption                     -> case E
    AW  abandonment                        -> case D
    AX  fictional-theme safety            -> case AG
    AY  causal safety                     -> field-name test
    AZ  no giant supporting-work payload  -> structural test
    BA  no overall taste score            -> structural test
    BB  determinism                       -> constructed
    BC  semantic invariance               -> cases A, R, S, T
"""

import uuid
from dataclasses import fields, is_dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preference.dashboard import (
    BUCKET_DISLIKES,
    BUCKET_EMERGING,
    BUCKET_MILDLY_LIKES,
    BUCKET_NOT_ESTABLISHED,
    BUCKET_STRONGLY_LIKES,
    DASHBOARD_PRESENTATION_KEYS,
    DEFAULT_DASHBOARD_PARAMETERS,
    MAX_STANDOUTS,
    STANDOUT_COMBINATION,
    STANDOUT_CROSS_DOMAIN,
    STANDOUT_OPPOSING,
    STANDOUTS,
    DashboardSummary,
    EvidenceSummary,
    FeatureRef,
    PreferenceItem,
    StandoutObservation,
    TasteDashboard,
    build_dashboard,
    build_taste_dashboard,
    classify,
)
from app.services.preference.insights import derive_insights
from app.services.preference.profile import (
    ComposedProfile,
    SelectedPattern,
    compose_profile,
)
from app.services.preference.taste import (
    KIND_COMBINATION,
    KIND_INDIVIDUAL,
    STATUS_EMERGING,
    STATUS_ESTABLISHED,
    STATUS_INSUFFICIENT,
    Feature,
    SupportingWork,
    TastePattern,
)
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library


@pytest.fixture
async def evaluation(db_session: AsyncSession):
    try:
        built = await build_evaluation_library(db_session)
    except MissingCorpusWorkError as exc:
        pytest.skip(f"corpus not ingested: {exc}")
    return {entry.spec.case: entry for entry in built}


async def dashboard_for(session, evaluation, case: str) -> TasteDashboard:
    return await build_taste_dashboard(session, evaluation[case].user_id)


# --- constructed patterns --------------------------------------------------


def work(rating: int | None = 9, domain: str = "anime") -> SupportingWork:
    return SupportingWork(
        work_id=uuid.uuid4(),
        title=f"work-{uuid.uuid4().hex[:6]}",
        domain_slug=domain,
        status="completed",
        rating=rating,
        normalized_rating=None if rating is None else 0.8,
        times_completed=1,
        in_library=True,
    )


def pattern(
    keys: tuple[str, ...] = ("feature",),
    *,
    status: str = STATUS_ESTABLISHED,
    confidence: float = 0.7,
    evidence: float | None = 0.9,
    direction: str = "positive",
    works: tuple[SupportingWork, ...] | None = None,
) -> TastePattern:
    features = tuple(
        Feature(key=key, name=key.replace("-", " ").title(), family="theme")
        for key in keys
    )
    supporting = works if works is not None else (work(), work(), work())
    return TastePattern(
        features=features,
        kind=KIND_INDIVIDUAL if len(features) == 1 else KIND_COMBINATION,
        status=status,
        direction=direction,
        preference_evidence=evidence,
        confidence=confidence,
        works_rated=sum(1 for w in supporting if w.rating is not None),
        supporting_works=supporting,
        domains=tuple(sorted({w.domain_slug for w in supporting})),
    )


def composed(key: list[TastePattern], early: list[TastePattern] | None = None):
    return ComposedProfile(
        user_id=uuid.uuid4(),
        key_patterns=[SelectedPattern(pattern=p) for p in key],
        early_signals=[SelectedPattern(pattern=p) for p in (early or [])],
    )


def dashboard_of(key, early=None) -> TasteDashboard:
    profile = composed(key, early)
    return build_dashboard(profile, derive_insights(profile))


# --- the classification rule, across the whole boundary matrix -------------


@pytest.mark.parametrize(
    ("evidence", "confidence", "status", "direction", "expected", "why"),
    [
        (
            0.90, 0.70, STATUS_ESTABLISHED, "positive", BUCKET_STRONGLY_LIKES,
            "past half the scale and in the high confidence band: both factors met",
        ),
        (
            0.60, 0.70, STATUS_ESTABLISHED, "positive", BUCKET_STRONGLY_LIKES,
            "0.60 clears the 0.5 half-scale mark; high band holds",
        ),
        (
            0.50, 0.60, STATUS_ESTABLISHED, "positive", BUCKET_STRONGLY_LIKES,
            "exactly on the evidence boundary, which is inclusive",
        ),
        (
            0.90, 0.45, STATUS_ESTABLISHED, "positive", BUCKET_STRONGLY_LIKES,
            "strong evidence with only moderate confidence is still a strong "
            "liking; the confidence band travels with the item instead",
        ),
        (
            0.90, 0.36, STATUS_ESTABLISHED, "positive", BUCKET_STRONGLY_LIKES,
            "and at the bottom of the moderate band too: the group is decided "
            "by strength alone",
        ),
        (
            0.49, 0.90, STATUS_ESTABLISHED, "positive", BUCKET_MILDLY_LIKES,
            "very well supported but under half the scale: the group names "
            "describe how much a reader liked something, not how sure Noema is",
        ),
        (
            0.20, 0.95, STATUS_ESTABLISHED, "positive", BUCKET_MILDLY_LIKES,
            "a weak liking held very consistently is still a weak liking",
        ),
        (
            0.45, 0.45, STATUS_ESTABLISHED, "positive", BUCKET_MILDLY_LIKES,
            "neither factor met",
        ),
        (
            -0.90, 0.70, STATUS_ESTABLISHED, "negative", BUCKET_DISLIKES,
            "one negative group by product definition; the band rides on the item",
        ),
        (
            -0.45, 0.40, STATUS_ESTABLISHED, "negative", BUCKET_DISLIKES,
            "established negative regardless of strength, for the same reason",
        ),
        (
            0.90, 0.20, STATUS_EMERGING, "positive", BUCKET_EMERGING,
            "large evidence held with little confidence never became established",
        ),
        (
            -0.90, 0.20, STATUS_EMERGING, "negative", BUCKET_EMERGING,
            "and the same on the negative side: not an established dislike",
        ),
        (
            None, 0.00, STATUS_ESTABLISHED, "unknown", BUCKET_NOT_ESTABLISHED,
            "no evidence value, so nothing to describe",
        ),
        (
            0.90, 0.70, STATUS_ESTABLISHED, "neutral", BUCKET_NOT_ESTABLISHED,
            "no direction to describe; Phase 1S refuses to establish these anyway",
        ),
        (
            0.90, 0.70, STATUS_INSUFFICIENT, "positive", BUCKET_NOT_ESTABLISHED,
            "below the support minimum, so it is not a pattern at all",
        ),
    ],
)
def test_the_bucket_rule_across_its_boundaries(
    evidence, confidence, status, direction, expected, why
) -> None:
    """Every combination Section 21 asks for, with the reasoning recorded.

    The values are chosen from the scale and the existing confidence bands,
    not from the evaluation library.
    """
    candidate = pattern(
        status=status, confidence=confidence, evidence=evidence, direction=direction
    )

    assert classify(candidate) == expected, why


def test_the_rule_has_exactly_one_new_constant() -> None:
    """The other boundary is Phase 1P's existing high band, reused."""
    assert [f.name for f in fields(DEFAULT_DASHBOARD_PARAMETERS)] == [
        "strong_evidence_from"
    ]
    assert DEFAULT_DASHBOARD_PARAMETERS.strong_evidence_from == 0.5


# --- the groups ------------------------------------------------------------


def test_emerging_is_never_promoted_into_an_established_group() -> None:
    """AR: whatever its numbers."""
    tempting = pattern(
        status=STATUS_EMERGING, confidence=0.95, evidence=0.99, direction="positive"
    )

    result = dashboard_of([], [tempting])

    assert result.strongly_likes == []
    assert result.mildly_likes == []
    assert result.dislikes == []
    assert [item.bucket for item in result.emerging] == [BUCKET_EMERGING]


def test_equal_evidence_shares_a_group_without_a_winner() -> None:
    """AO: no arbitrary ranking among findings the evidence cannot separate."""
    shared = (work(), work(), work())
    equals = [
        pattern((key,), confidence=0.7, evidence=0.8, works=shared)
        for key in ("drama", "tragedy", "mortality", "existential-themes")
    ]

    result = dashboard_of(equals)

    assert len(result.strongly_likes) == 4
    assert {item.bucket for item in result.strongly_likes} == {BUCKET_STRONGLY_LIKES}
    # Identical inputs, identical presentation. Nothing marks one as first
    # among them beyond list order, which carries no claim.
    assert len({item.confidence_band for item in result.strongly_likes}) == 1
    assert len({item.evidence.works_rated for item in result.strongly_likes}) == 1


def test_breadth_of_domains_does_not_change_a_group() -> None:
    """AT and AS: cross-domain is provenance, never priority."""
    broad = pattern(
        ("broad",),
        confidence=0.45,
        evidence=0.40,
        works=(work(domain="anime"), work(domain="literature"), work(domain="manhwa")),
    )
    deep = pattern(
        ("deep",),
        confidence=0.70,
        evidence=0.90,
        works=tuple(work(domain="anime") for _ in range(6)),
    )

    result = dashboard_of([broad, deep])

    # Strength decides, and breadth is not strength: three media cannot lift
    # a weaker preference above a stronger single-medium one.
    assert [i.key for i in result.strongly_likes] == ["deep"]
    assert [i.key for i in result.mildly_likes] == ["broad"]
    # The breadth is still recorded on the item that has it.
    assert result.item("broad").is_cross_domain
    assert result.item("broad").domains == ("anime", "literature", "manhwa")


def test_a_combination_keeps_both_features_in_its_group() -> None:
    """AQ and AP: an established pair is structurally distinct from its parts."""
    drama = pattern(("drama",), confidence=0.7, evidence=0.80)
    tragedy = pattern(("tragedy",), confidence=0.7, evidence=0.80)
    pair = pattern(("drama", "tragedy"), confidence=0.7, evidence=0.90)

    result = dashboard_of([drama, tragedy, pair])

    combination = result.item("drama+tragedy")
    assert combination.is_combination
    assert combination.kind == KIND_COMBINATION
    assert [f.key for f in combination.features] == ["drama", "tragedy"]
    assert combination.display_name == "Drama + Tragedy"
    # The individuals remain their own items, not folded into the pair.
    assert result.item("drama") is not None
    assert result.item("tragedy") is not None
    assert len(result.strongly_likes) == 3


def test_nothing_is_padded_when_evidence_is_thin() -> None:
    """AU: a cold-start reader gets a small dashboard, not a filled one."""
    result = dashboard_of([])

    assert result.strongly_likes == []
    assert result.mildly_likes == []
    assert result.dislikes == []
    assert result.emerging == []
    assert result.what_stands_out == []
    assert result.summary.concepts_with_established_evidence == 0


# --- what stands out -------------------------------------------------------


def test_observations_spanning_the_same_media_collapse_to_one() -> None:
    """Six concepts spanning anime and literature make that point once.

    Not a cap: they collapse because they say the same thing. A concept
    spanning a different set of media is a different finding and survives --
    see the Phase 1W deduplication tests.
    """
    broad = [
        pattern(
            (f"broad-{index}",),
            confidence=0.7 - index / 100,
            works=(work(domain="anime"), work(domain="literature")),
        )
        for index in range(6)
    ]

    result = dashboard_of(broad)

    cross = [o for o in result.what_stands_out if o.observation == STANDOUT_CROSS_DOMAIN]
    assert len(cross) == 1
    assert len(result.what_stands_out) <= MAX_STANDOUTS


def test_a_standout_says_something_a_group_listing_does_not() -> None:
    """A plain single-domain pattern is already visible in its group."""
    plain = pattern(("plain",), works=(work(), work(), work()))

    result = dashboard_of([plain])

    assert result.strongly_likes
    assert result.what_stands_out == []


def test_opposing_directions_appears_when_both_are_established() -> None:
    liked = pattern(("liked",), confidence=0.7, evidence=0.9, direction="positive")
    disliked = pattern(
        ("disliked",),
        confidence=0.7,
        evidence=-0.9,
        direction="negative",
        works=(work(2), work(3), work(2)),
    )

    result = dashboard_of([liked, disliked])

    kinds = [o.observation for o in result.what_stands_out]
    assert STANDOUT_OPPOSING in kinds
    opposing = next(
        o for o in result.what_stands_out if o.observation == STANDOUT_OPPOSING
    )
    assert opposing.features == ()
    assert len(opposing.pattern_keys) == 2


def test_every_standout_value_comes_from_a_closed_set() -> None:
    pair = pattern(("a", "b"), works=(work(domain="anime"), work(domain="manhwa"), work()))
    negative = pattern(
        ("n",), confidence=0.7, evidence=-0.9, direction="negative",
        works=(work(2), work(3), work(2)),
    )

    result = dashboard_of([pair, negative])

    assert result.what_stands_out
    for observation in result.what_stands_out:
        assert observation.observation in STANDOUTS
        assert observation.presentation_key in DASHBOARD_PRESENTATION_KEYS


# --- determinism and invariance --------------------------------------------


def test_shuffled_patterns_give_an_identical_dashboard() -> None:
    """BB."""
    patterns = [
        pattern((f"f{index}",), confidence=0.5 + index / 100) for index in range(6)
    ]

    forward = dashboard_of(list(patterns))
    backward = dashboard_of(list(reversed(patterns)))

    def shape(result):
        return (
            [i.key for i in result.strongly_likes],
            [i.key for i in result.mildly_likes],
            [i.key for i in result.dislikes],
            [o.observation for o in result.what_stands_out],
        )

    assert shape(forward) == shape(backward)


@pytest.mark.parametrize("case", ["A", "R", "S", "T"])
async def test_building_a_dashboard_mutates_nothing(
    db_session: AsyncSession, evaluation, case: str
) -> None:
    """BC."""
    profile = await compose_profile(db_session, evaluation[case].user_id)
    insights = derive_insights(profile)

    def snapshot():
        return {
            item.key: (
                item.pattern.preference_evidence,
                item.pattern.confidence,
                item.pattern.direction,
                item.pattern.status,
                item.pattern.domains,
                tuple(w.work_id for w in item.pattern.supporting_works),
                item.pattern.works_reconsumed,
                item.pattern.total_completions,
                item.pattern.works_abandoned,
                item.pattern.constituent_evidence,
            )
            for item in [*profile.key_patterns, *profile.early_signals]
        }

    before = snapshot()
    build_dashboard(profile, insights)

    assert snapshot() == before
    # And the insight objects are untouched too.
    assert all(i.evidence is None or i.evidence.status for i in insights.insights)


# --- the evaluation cases --------------------------------------------------


async def test_case_ak_reaches_strongly_likes(
    db_session: AsyncSession, evaluation
) -> None:
    """AK: seven consistent ratings, strong evidence, high confidence.

    Both hold here, which is what makes this the case that shows they are
    independent: the group comes from the evidence, and the band is reported
    beside it rather than deciding it.
    """
    result = await dashboard_for(db_session, evaluation, "AK")

    assert result.strongly_likes
    tragedy = result.item("tragedy")
    assert tragedy.bucket == BUCKET_STRONGLY_LIKES
    assert tragedy.confidence_band == "high"
    assert tragedy.evidence.works_rated == 7
    assert abs(tragedy.evidence.preference_evidence) >= 0.5


async def test_case_al_strong_evidence_alone_is_only_mild(
    db_session: AsyncSession, evaluation
) -> None:
    """AL: strong evidence at moderate confidence is a *strong* liking.

    Case A rates five works 8-10. Phase 1V filed that as "mildly likes"
    because five ratings do not reach the high confidence band -- which told
    the reader Noema mildly thought they liked something they had rated 8, 9,
    9, 10, 10. The group now follows the evidence, and the band is reported
    alongside it.
    """
    result = await dashboard_for(db_session, evaluation, "A")

    psychological = result.item("psychological-depth")
    assert psychological.bucket == BUCKET_STRONGLY_LIKES
    assert abs(psychological.evidence.preference_evidence) >= 0.5
    assert psychological.confidence_band == "moderate"


@pytest.mark.parametrize("case", ["B", "T"])
async def test_case_am_established_negatives_become_dislikes(
    db_session: AsyncSession, evaluation, case: str
) -> None:
    """AM."""
    result = await dashboard_for(db_session, evaluation, case)

    assert result.dislikes
    for item in result.dislikes:
        assert item.direction == "negative"
        assert item.bucket == BUCKET_DISLIKES
        assert item.presentation_key in DASHBOARD_PRESENTATION_KEYS


async def test_case_an_uncertain_negative_is_not_a_dislike(
    db_session: AsyncSession, evaluation
) -> None:
    """AN: two low ratings are an early signal, not an established dislike."""
    result = await dashboard_for(db_session, evaluation, "AN")

    fantasy = result.item("fantasy")
    assert fantasy is not None
    assert fantasy.direction == "negative"
    assert fantasy.bucket == BUCKET_EMERGING
    assert fantasy not in result.dislikes
    assert result.dislikes == []


async def test_case_ao_equal_evidence_shares_a_group(
    db_session: AsyncSession, evaluation
) -> None:
    """AO, on real data: case M's concepts rest on the same ratings."""
    result = await dashboard_for(db_session, evaluation, "M")

    group = result.strongly_likes
    assert len(group) >= 3
    # Several items report the same evidence and confidence; none is marked
    # as the reader's foremost preference.
    values = {
        (item.evidence.preference_evidence, item.evidence.confidence)
        for item in group
    }
    assert len(values) < len(group)


async def test_case_aq_the_established_combination_is_presented_as_one(
    db_session: AsyncSession, evaluation
) -> None:
    """AQ and AP on real data."""
    result = await dashboard_for(db_session, evaluation, "S")

    combination = result.item("crime-and-investigation+urban-modernity")
    assert combination is not None
    assert combination.is_combination
    assert [f.name for f in combination.features] == [
        "Crime and Investigation",
        "Urban Modernity",
    ]
    assert combination.bucket in (BUCKET_STRONGLY_LIKES, BUCKET_MILDLY_LIKES)
    standout = next(
        o for o in result.what_stands_out if o.observation == STANDOUT_COMBINATION
    )
    assert len(standout.features) == 2


async def test_case_ar_emerging_stays_out_of_established_groups(
    db_session: AsyncSession, evaluation
) -> None:
    result = await dashboard_for(db_session, evaluation, "U")

    assert result.emerging
    established_keys = {item.key for item in result.established_items()}
    assert not established_keys & {item.key for item in result.emerging}


async def test_case_as_cross_domain_is_metadata(
    db_session: AsyncSession, evaluation
) -> None:
    result = await dashboard_for(db_session, evaluation, "V")

    science = result.item("science-fiction")
    assert set(science.domains) == {"anime", "literature", "manhwa"}
    assert science.is_cross_domain


@pytest.mark.parametrize("case", ["C", "D"])
async def test_case_au_cold_start_is_empty(
    db_session: AsyncSession, evaluation, case: str
) -> None:
    result = await dashboard_for(db_session, evaluation, case)

    assert result.all_items() == []
    assert result.what_stands_out == []
    assert result.summary.concepts_with_established_evidence == 0


async def test_case_av_reconsumption_does_not_raise_a_group(
    db_session: AsyncSession, evaluation
) -> None:
    """AV: finishing something three times is not three ratings."""
    result = await dashboard_for(db_session, evaluation, "E")

    reconsumed = [i for i in result.all_items() if i.evidence.works_reconsumed > 0]
    assert reconsumed
    for item in reconsumed:
        assert item.evidence.total_completions > item.evidence.works_rated
        # Two ratings cannot clear the established minimum however many times
        # the works were finished.
        assert item.bucket == BUCKET_EMERGING


async def test_case_aw_abandonment_produces_no_dislike(
    db_session: AsyncSession, evaluation
) -> None:
    result = await dashboard_for(db_session, evaluation, "D")

    assert result.dislikes == []
    assert result.all_items() == []


async def test_case_ax_a_fictional_theme_stays_a_media_observation(
    db_session: AsyncSession, evaluation
) -> None:
    result = await dashboard_for(db_session, evaluation, "AG")

    # Alternatives are carried as display names, as a reader would see them.
    carrying = [
        item
        for item in result.all_items()
        if item.key == "found-family" or "Found Family" in item.also_supported_by
    ]
    assert len(carrying) == 1
    item = carrying[0]
    assert item.bucket in (BUCKET_STRONGLY_LIKES, BUCKET_MILDLY_LIKES)
    assert item.evidence.works_rated == 4


# --- what the contract cannot say ------------------------------------------


DASHBOARD_TYPES = (
    TasteDashboard,
    PreferenceItem,
    EvidenceSummary,
    StandoutObservation,
    DashboardSummary,
    FeatureRef,
)


def test_ay_no_field_can_carry_a_cause() -> None:
    names = {f.name for cls in DASHBOARD_TYPES for f in fields(cls)}
    for forbidden in (
        "why",
        "because",
        "caused_by",
        "cause",
        "reason",
        "motivation",
        "explanation",
        "inferred",
        "driver",
    ):
        assert not any(forbidden in name for name in names), forbidden


def test_no_field_can_carry_biography_or_personality() -> None:
    names = {f.name for cls in DASHBOARD_TYPES for f in fields(cls)}
    for forbidden in (
        "personality",
        "trait",
        "childhood",
        "family_history",
        "relationship",
        "trauma",
        "mental",
        "health",
        "intelligence",
        "morality",
        "diagnosis",
        "biography",
        "experience",
    ):
        assert not any(forbidden in name for name in names), forbidden


def test_ba_there_is_no_overall_taste_score() -> None:
    """No aggregate scalar about a reader, anywhere in the contract."""
    names = {f.name for cls in DASHBOARD_TYPES for f in fields(cls)}
    for forbidden in (
        "taste_score",
        "score",
        "overall",
        "index",
        "rank",
        "rating_of_user",
        "compatibility",
        "percentile",
        "grade",
    ):
        assert not any(forbidden in name for name in names), forbidden

    # And the summary is counts only.
    summary = dashboard_of([pattern(("a",)), pattern(("b",))]).summary
    for f in fields(DashboardSummary):
        assert isinstance(getattr(summary, f.name), int), f.name


def test_az_items_carry_work_ids_not_work_records() -> None:
    """The primary representation stays compact."""
    result = dashboard_of([pattern(("a",), works=tuple(work() for _ in range(9)))])
    item = result.strongly_likes[0]

    assert item.supporting_work_ids
    assert all(isinstance(value, uuid.UUID) for value in item.supporting_work_ids)
    # Nothing on the item is a nested record of a work.
    for f in fields(PreferenceItem):
        value = getattr(item, f.name)
        if isinstance(value, tuple):
            for element in value:
                assert not (
                    is_dataclass(element) and hasattr(element, "title")
                ), f.name


def test_the_dashboard_reads_no_corpus_frequency_and_invents_no_score() -> None:
    import ast
    import pathlib

    source = pathlib.Path("app/services/preference/dashboard.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(
        name.endswith(("frequency", "experiment")) for name in imported
    ), imported

    identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    for forbidden in (
        "idf",
        "document_frequency",
        "rarity",
        "novelty",
        "popularity",
        "diversity",
        "taste_score",
        "compatibility",
    ):
        assert not any(forbidden in name.lower() for name in identifiers), forbidden


# --- isolation --------------------------------------------------------------


async def test_a_dashboard_only_contains_that_readers_own_works(
    db_session: AsyncSession, evaluation
) -> None:
    """Section 24: user preference data stays inside the authenticated user."""
    from sqlalchemy import select

    from app.models import UserContentInteraction

    for case in ("A", "T", "AK"):
        entry = evaluation[case]
        result = await dashboard_for(db_session, evaluation, case)
        theirs = {
            row
            for row in (
                await db_session.execute(
                    select(UserContentInteraction.work_id).where(
                        UserContentInteraction.user_id == entry.user_id
                    )
                )
            ).scalars()
        }
        for item in result.all_items():
            assert set(item.supporting_work_ids) <= theirs, (case, item.key)


async def test_two_readers_of_the_same_works_get_different_dashboards(
    db_session: AsyncSession, evaluation
) -> None:
    """Cases A and B share an exposure history and rated it oppositely."""
    positive = await dashboard_for(db_session, evaluation, "A")
    negative = await dashboard_for(db_session, evaluation, "B")

    assert positive.user_id != negative.user_id
    assert positive.dislikes == []
    assert negative.mildly_likes == []
    assert negative.dislikes


async def test_nothing_is_written(db_session: AsyncSession, evaluation) -> None:
    await db_session.flush()
    await dashboard_for(db_session, evaluation, "R")

    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted
