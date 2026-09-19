"""Phase 1Q: the inverse-concept-frequency experiment.

Two groups. The first builds controlled corpora where the frequency of one
concept is the only thing that varies, so the weight's effect can be read
directly. The second replays the Phase 1N evaluation cases and checks that
every semantic rule Phase 1O established survives the experimental layer --
which it must by construction, since that layer copies baseline values rather
than recomputing them, and these tests are what hold it to that.

Nothing here touches the production path. `evidence.py` is untouched and
`/preferences` continues to read it.
"""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import STATUS_ABANDONED, STATUS_COMPLETED, STATUS_ON_HOLD, Work
from app.services import auth_service, library_service
from app.services.concepts.service import SourceLabel, apply_source_labels, ensure_vocabulary
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.service import ingest_source_work
from app.services.preference.evidence import build_preference_profile
from app.services.preference.experiment import build_weighted_profile, weight_concept
from app.services.preference.frequency import FrequencyParameters, build_frequencies
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library

FIXTURES = Path(__file__).parent / "fixtures"
TEST_ID_OFFSET = 990000
PASSWORD = "a-sufficiently-long-password"


@pytest.fixture
async def evaluation(db_session: AsyncSession):
    try:
        built = await build_evaluation_library(db_session)
    except MissingCorpusWorkError as exc:
        pytest.skip(f"corpus not ingested: {exc}")
    return {entry.spec.case: entry for entry in built}


async def make_work(session: AsyncSession, offset: int, labels: list[str]) -> Work:
    media = json.loads((FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8"))
    media["id"] = TEST_ID_OFFSET + offset
    media["relations"] = {"edges": []}
    media["genres"] = []
    media["tags"] = []
    result = await ingest_source_work(session, AniListAnimeAdapter(media=media).load())
    work = await session.get(Work, result.work_id)
    if labels:
        await apply_source_labels(
            session,
            work=work,
            labels=[SourceLabel(label, "anilist_tag", rank=80) for label in labels],
        )
    return work


async def rate(session: AsyncSession, user, work: Work, rating: int | None) -> None:
    await library_service.add_to_library(session, user_id=user.id, work_id=work.id)
    await library_service.set_status(
        session, user_id=user.id, work_id=work.id, status=STATUS_COMPLETED
    )
    if rating is not None:
        await library_service.set_rating(
            session, user_id=user.id, work_id=work.id, rating=rating
        )


# =========================================================================
# Controlled: common versus specific
# =========================================================================


def test_a_less_common_concept_carries_more_per_work_weight() -> None:
    """The experiment's central claim, isolated from everything else.

    Two concepts, identical in every respect except how many corpus works
    carry them. The rarer one must weigh more.
    """
    frequencies = build_frequencies({"common": 12, "specific": 3}, 17)

    assert (
        frequencies.by_slug["specific"].specificity
        > frequencies.by_slug["common"].specificity
    )


@pytest.mark.xfail(strict=True, reason="Phase 1Q result: the salience formulation fails this requirement. A df=1 concept with one 10/10 scores 0.250 against 0.186 for a df=11 concept with four agreeing 9s. The requirement is only met at smoothing k >= 10, where the idf range has collapsed from 2.27x to 1.52x and the weight is close to inert. Recorded as a strict xfail so the negative result stays visible; if a later formulation passes, this will XPASS and demand removal.")
def test_a_common_concept_backed_by_many_strong_ratings_does_not_disappear() -> None:
    """Being common must not be disqualifying.

    A ubiquitous concept with five agreeing 9s should still out-rank a rare
    one resting on a single rating -- the weight tilts a ranking, it does not
    decide it.
    """
    frequencies = build_frequencies({"common": 15, "rare": 1}, 17)

    class Stub:
        def __init__(self, slug, evidence, confidence, rated):
            self.concept_slug = slug
            self.concept_name = slug
            self.preference_evidence = evidence
            self.direction = "positive"
            self.confidence = confidence
            self.works_rated = rated

    # Five agreeing ratings against one.
    well_supported = weight_concept(Stub("common", 0.83, 0.54, 5), frequencies)
    thin = weight_concept(Stub("rare", 0.83, 0.25, 1), frequencies)

    assert well_supported.salience > thin.salience


@pytest.mark.xfail(strict=True, reason="Phase 1Q result: the salience formulation fails this requirement. A df=1 concept with one 10/10 scores 0.250 against 0.186 for a df=11 concept with four agreeing 9s. The requirement is only met at smoothing k >= 10, where the idf range has collapsed from 2.27x to 1.52x and the weight is close to inert. Recorded as a strict xfail so the negative result stays visible; if a later formulation passes, this will XPASS and demand removal.")
def test_a_single_work_concept_does_not_become_dominant_from_one_rating() -> None:
    """The pathological case the phase was warned about.

    df = 1 earns the maximum specificity, so the only thing keeping it in
    proportion is that one rating buys very little confidence. That is the
    damping mechanism, and it has to be strong enough.
    """
    frequencies = build_frequencies({"unique": 1, "common": 11}, 17)

    class Stub:
        def __init__(self, slug, evidence, confidence, rated):
            self.concept_slug = slug
            self.concept_name = slug
            self.preference_evidence = evidence
            self.direction = "positive"
            self.confidence = confidence
            self.works_rated = rated

    unique = weight_concept(Stub("unique", 1.0, 0.25, 1), frequencies)
    common = weight_concept(Stub("common", 0.80, 0.48, 4), frequencies)

    assert unique.specificity == pytest.approx(1.0)
    # Four agreeing ratings beat one, despite the rarest possible concept.
    assert common.salience > unique.salience
    # And the weight itself never exceeds the bound.
    assert unique.salience <= 1.0


async def test_frequency_changes_weight_without_changing_evidence(
    db_session: AsyncSession,
) -> None:
    """The same rating on a rare and a common concept: same evidence, different weight."""
    await ensure_vocabulary(db_session)
    user = await auth_service.register_user(
        db_session, email="controlled@idf.test", password=PASSWORD
    )
    # One work carrying both a common and a rarer concept, rated once.
    subject = await make_work(db_session, 10, ["Psychological", "Revenge"])
    await rate(db_session, user, subject, 9)
    # Pad the corpus so "Psychological" becomes the more common of the two.
    for index in range(6):
        await make_work(db_session, 20 + index, ["Psychological"])

    profile = await build_weighted_profile(db_session, user.id)
    by_slug = profile.by_slug()
    common = by_slug["psychological-depth"]
    rarer = by_slug["revenge"]

    # One work, one rating: the evidence is identical.
    assert common.preference_evidence == rarer.preference_evidence
    assert common.confidence == rarer.confidence
    # Only the corpus-level weight differs.
    assert common.document_frequency > rarer.document_frequency
    assert rarer.specificity > common.specificity
    assert rarer.salience > common.salience


# =========================================================================
# The naive variant this phase rejects
# =========================================================================


async def test_the_naive_variant_leaves_the_bounded_range(
    db_session: AsyncSession, evaluation
) -> None:
    """`evidence * idf` breaks the contract the product layer rests on.

    Direction, the neutral band and the whole Phase 1P surface assume
    evidence lies in [-1, 1]. Reported rather than asserted in prose, so the
    rejection rests on measured values.
    """
    profile = await build_weighted_profile(db_session, evaluation["A"].user_id)
    rated = [item for item in profile.concepts if item.works_rated > 0]

    assert rated
    out_of_range = [item for item in rated if item.naive_out_of_range]
    assert out_of_range, "expected the naive variant to breach [-1, 1]"
    # Meanwhile the experimental salience stays bounded throughout.
    assert all(0.0 <= item.salience <= 1.0 for item in rated)


def test_per_work_weighting_is_algebraically_post_hoc_scaling() -> None:
    """There is no aggregation boundary where per-work idf behaves differently.

    idf(c) is constant across the works carrying c, and Phase 1O takes a
    mean, so weighting each contribution is exactly scaling the result. This
    is why the experiment does not weight inside the aggregation.
    """
    from statistics import fmean

    ratings = [0.83, 0.56, 0.91, 0.78]
    weight = 2.6

    assert fmean([value * weight for value in ratings]) == pytest.approx(
        fmean(ratings) * weight
    )


# =========================================================================
# Every Phase 1O semantic rule survives the experimental layer
# =========================================================================


async def test_baseline_values_pass_through_untouched(
    db_session: AsyncSession, evaluation
) -> None:
    """The control: if any baseline field differs, the experiment is invalid."""
    for case in ("A", "B", "E", "G", "H", "I"):
        baseline = await build_preference_profile(db_session, evaluation[case].user_id)
        weighted = (
            await build_weighted_profile(db_session, evaluation[case].user_id)
        ).by_slug()

        for item in baseline.concepts:
            experimental = weighted[item.concept_slug]
            assert experimental.preference_evidence == item.preference_evidence
            assert experimental.direction == item.direction
            assert experimental.confidence == item.confidence
            assert experimental.works_rated == item.works_rated


async def test_case_a_stays_clearly_positive(
    db_session: AsyncSession, evaluation
) -> None:
    profile = (await build_weighted_profile(db_session, evaluation["A"].user_id)).by_slug()

    assert profile["psychological-depth"].direction == "positive"
    assert profile["psychological-depth"].preference_evidence > 0


async def test_case_b_stays_clearly_negative(
    db_session: AsyncSession, evaluation
) -> None:
    """Weighting must not rescue a concept the user rated badly."""
    profile = (await build_weighted_profile(db_session, evaluation["B"].user_id)).by_slug()

    assert profile["psychological-depth"].direction == "negative"
    assert profile["psychological-depth"].preference_evidence < 0
    # Salience is a magnitude: it says "worth showing", not "liked".
    assert profile["psychological-depth"].salience > 0


async def test_cases_c_and_d_stay_directionally_unknown(
    db_session: AsyncSession, evaluation
) -> None:
    for case in ("C", "D"):
        profile = await build_weighted_profile(db_session, evaluation[case].user_id)
        assert profile.concepts
        for item in profile.concepts:
            assert item.direction == "unknown"
            assert item.preference_evidence is None
            # No direction means nothing to be salient about.
            assert item.salience == 0.0
            assert item.naive_weighted_evidence is None


async def test_case_e_keeps_reconsumption_out_of_the_weighted_signal(
    db_session: AsyncSession, evaluation
) -> None:
    baseline = await build_preference_profile(db_session, evaluation["E"].user_id)
    weighted = (await build_weighted_profile(db_session, evaluation["E"].user_id)).by_slug()

    engine = {item.concept_slug: item for item in baseline.concepts}
    target = "crime-and-investigation"

    assert engine[target].works_reconsumed == 1
    # The rating channel is the same figure it was, reconsumption or not.
    assert weighted[target].preference_evidence == engine[target].preference_evidence


async def test_case_g_keeps_its_cross_domain_signal(
    db_session: AsyncSession, evaluation
) -> None:
    baseline = await build_preference_profile(db_session, evaluation["G"].user_id)
    weighted = (await build_weighted_profile(db_session, evaluation["G"].user_id)).by_slug()

    engine = {item.concept_slug: item for item in baseline.concepts}
    domains = {item.domain_slug for item in engine["science-fiction"].contributions}

    assert domains == {"anime", "literature", "manhwa"}
    assert weighted["science-fiction"].direction == "positive"
    assert weighted["science-fiction"].preference_evidence > 0


async def test_case_h_personal_rating_distribution_still_governs_direction(
    db_session: AsyncSession, evaluation
) -> None:
    """A harsh rater's 6s and 7s stay positive; frequency does not touch that."""
    weighted = (await build_weighted_profile(db_session, evaluation["H"].user_id)).by_slug()

    assert weighted["science-fiction"].direction == "positive"
    negatives = [
        item
        for item in weighted.values()
        if item.direction == "negative" and item.works_rated >= 2
    ]
    assert negatives


async def test_case_i_keeps_evidence_after_removal(
    db_session: AsyncSession, evaluation
) -> None:
    weighted = (await build_weighted_profile(db_session, evaluation["I"].user_id)).by_slug()

    assert weighted["science-fiction"].direction == "positive"
    assert weighted["science-fiction"].works_rated > 0


# =========================================================================
# Confidence, and the production boundary
# =========================================================================


async def test_frequency_never_inflates_confidence(
    db_session: AsyncSession, evaluation
) -> None:
    """Rarity says nothing about how sure we are what the *user* thought.

    Confidence is volume times agreement over the user's own ratings. If a
    rare concept could buy confidence, the experiment would be manufacturing
    certainty out of a corpus statistic.
    """
    baseline = await build_preference_profile(db_session, evaluation["A"].user_id)
    weighted = (await build_weighted_profile(db_session, evaluation["A"].user_id)).by_slug()

    engine = {item.concept_slug: item for item in baseline.concepts}
    for slug, item in weighted.items():
        assert item.confidence == engine[slug].confidence

    # And confidence does not track specificity.
    rarest = max(weighted.values(), key=lambda item: item.specificity)
    assert rarest.confidence <= max(item.confidence for item in weighted.values())


async def test_the_experiment_is_absent_from_the_product_response(
    db_session: AsyncSession, evaluation
) -> None:
    """Phase 1P's surface must be untouched by this phase."""
    from app.services.preference.product import build_preference_overview

    overview = await build_preference_overview(db_session, evaluation["A"].user_id)
    serialised = json.dumps(overview.model_dump(mode="json"))

    for forbidden in (
        "salience",
        "specificity",
        "idf",
        "document_frequency",
        "naive_weighted",
    ):
        assert forbidden not in serialised


async def test_weighted_profiles_are_deterministic(
    db_session: AsyncSession, evaluation
) -> None:
    first = await build_weighted_profile(db_session, evaluation["A"].user_id)
    second = await build_weighted_profile(db_session, evaluation["A"].user_id)

    assert [
        (item.concept_slug, item.salience, item.specificity) for item in first.concepts
    ] == [(item.concept_slug, item.salience, item.specificity) for item in second.concepts]


async def test_an_unknown_user_yields_an_empty_weighted_profile(
    db_session: AsyncSession,
) -> None:
    profile = await build_weighted_profile(db_session, uuid.uuid4())

    assert profile.concepts == []
    assert profile.frequencies.total_works > 0


@pytest.mark.parametrize("smoothing", [1.0, 2.0, 5.0])
async def test_the_conclusions_hold_under_every_smoothing(
    db_session: AsyncSession, evaluation, smoothing: float
) -> None:
    """No finding in this phase depends on a particular k."""
    parameters = FrequencyParameters(smoothing=smoothing)

    positive = (
        await build_weighted_profile(
            db_session, evaluation["A"].user_id, frequency_parameters=parameters
        )
    ).by_slug()
    negative = (
        await build_weighted_profile(
            db_session, evaluation["B"].user_id, frequency_parameters=parameters
        )
    ).by_slug()

    assert positive["psychological-depth"].direction == "positive"
    assert negative["psychological-depth"].direction == "negative"
    assert all(0.0 <= item.salience <= 1.0 for item in positive.values())
