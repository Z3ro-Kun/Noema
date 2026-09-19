"""The preference evidence engine, against the Phase 1N evaluation library.

Two groups of tests. The first replays each documented evaluation case and
checks the engine reads it the way the fixture says it should. The second is
adversarial: explicit attempts to make consumption masquerade as preference,
each of which must fail.

Fixture-scoped and rolled back. The small synthetic fixtures build their own
works rather than relying on the production corpus; only the evaluation-case
tests use the real one, because the concept associations that give those
cases their meaning are real.
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
from app.services.preference.evidence import (
    DIRECTION_NEGATIVE,
    DIRECTION_POSITIVE,
    DIRECTION_UNKNOWN,
    build_preference_profile,
    concept_evidence_for,
)
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library

FIXTURES = Path(__file__).parent / "fixtures"
TEST_ID_OFFSET = 980000
PASSWORD = "a-sufficiently-long-password"


@pytest.fixture
async def evaluation(db_session: AsyncSession):
    try:
        built = await build_evaluation_library(db_session)
    except MissingCorpusWorkError as exc:
        pytest.skip(f"corpus not ingested: {exc}")
    return {entry.spec.case: entry for entry in built}


def evidence_by_slug(profile):
    return {item.concept_slug: item for item in profile.concepts}


# --- small local fixtures, independent of the corpus -----------------------


async def make_work(session: AsyncSession, offset: int, labels: list[str]) -> Work:
    """A throwaway work carrying exactly the concepts named."""
    media = json.loads((FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8"))
    media["id"] = TEST_ID_OFFSET + offset
    media["relations"] = {"edges": []}
    media["genres"] = []
    media["tags"] = []
    result = await ingest_source_work(session, AniListAnimeAdapter(media=media).load())
    work = await session.get(Work, result.work_id)
    await apply_source_labels(
        session,
        work=work,
        labels=[SourceLabel(label, "anilist_tag", rank=80) for label in labels],
    )
    return work


async def make_user(session: AsyncSession, name: str):
    return await auth_service.register_user(
        session, email=f"{name}@preference.test", password=PASSWORD
    )


async def record(
    session: AsyncSession,
    user,
    work: Work,
    *,
    statuses: tuple[str, ...] = (STATUS_COMPLETED,),
    rating: int | None = None,
    repeats: int = 0,
) -> None:
    await library_service.add_to_library(session, user_id=user.id, work_id=work.id)
    for status in statuses:
        await library_service.set_status(
            session, user_id=user.id, work_id=work.id, status=status
        )
    for _ in range(repeats):
        await library_service.set_status(
            session, user_id=user.id, work_id=work.id, status="in_progress"
        )
        await library_service.set_status(
            session, user_id=user.id, work_id=work.id, status=STATUS_COMPLETED
        )
    if rating is not None:
        await library_service.set_rating(
            session, user_id=user.id, work_id=work.id, rating=rating
        )


# =========================================================================
# The documented evaluation cases
# =========================================================================


async def test_case_a_and_b_have_identical_exposure_and_opposite_evidence(
    db_session: AsyncSession, evaluation
) -> None:
    """The load-bearing test of the whole phase: consumption is not preference."""
    a = await concept_evidence_for(
        db_session, evaluation["A"].user_id, "psychological-depth"
    )
    b = await concept_evidence_for(
        db_session, evaluation["B"].user_id, "psychological-depth"
    )

    # Identical consumption, down to the counts.
    assert a.works_exposed == b.works_exposed
    assert a.works_completed == b.works_completed
    assert a.exposure == b.exposure
    assert a.engagement == b.engagement

    # Opposite conclusions.
    assert a.direction == DIRECTION_POSITIVE
    assert b.direction == DIRECTION_NEGATIVE
    assert a.preference_evidence > 0 > b.preference_evidence
    assert a.positive_ratings == 5 and a.negative_ratings == 0
    assert b.positive_ratings == 0 and b.negative_ratings == 5


async def test_case_c_reports_engagement_but_invents_no_preference(
    db_session: AsyncSession, evaluation
) -> None:
    """Completed and unrated: consumption is visible, preference is not guessed."""
    evidence = await concept_evidence_for(
        db_session, evaluation["C"].user_id, "crime-and-investigation"
    )

    assert evidence.works_completed == 3
    assert evidence.engagement > 0
    assert evidence.exposure > 0

    assert evidence.preference_evidence is None
    assert evidence.rating_signal is None
    assert evidence.direction == DIRECTION_UNKNOWN
    assert evidence.confidence == 0.0


async def test_case_d_keeps_abandonment_ambiguous_and_separate_from_on_hold(
    db_session: AsyncSession, evaluation
) -> None:
    profile = await build_preference_profile(db_session, evaluation["D"].user_id)
    with_abandonment = [item for item in profile.concepts if item.works_abandoned]

    assert with_abandonment
    for evidence in with_abandonment:
        assert evidence.abandonment_signal > 0
        # Abandonment never becomes a rating, and never a direction.
        assert evidence.preference_evidence is None
        assert evidence.direction == DIRECTION_UNKNOWN
        assert evidence.ratings == []

    on_hold = [item for item in profile.concepts if item.works_on_hold]
    assert on_hold
    for evidence in on_hold:
        if evidence.works_abandoned == 0:
            assert evidence.abandonment_signal == 0.0


async def test_case_e_separates_reconsumption_from_the_rating(
    db_session: AsyncSession, evaluation
) -> None:
    """Same rating, different behaviour: two channels, not one number."""
    evidence = await concept_evidence_for(
        db_session, evaluation["E"].user_id, "crime-and-investigation"
    )
    by_title = {item.title: item for item in evidence.contributions}
    repeated = next(item for item in by_title.values() if item.times_completed > 1)
    once = next(item for item in by_title.values() if item.times_completed == 1)

    # The explicit rating is identical and is preserved as such.
    assert repeated.rating == once.rating == 9
    assert repeated.normalized_rating == once.normalized_rating
    # The behavioural channel is where the difference shows.
    assert evidence.reconsumption_signal > 0
    assert evidence.works_reconsumed == 1
    assert evidence.total_completions > evidence.works_completed


async def test_case_f_does_not_invent_a_concept_preference_from_mixed_ratings(
    db_session: AsyncSession, evaluation
) -> None:
    """Four works, no shared concept, ratings 9/3/7/5: nothing should be confident."""
    profile = await build_preference_profile(db_session, evaluation["F"].user_id)

    assert profile.concepts
    # No concept is supported by enough agreeing ratings to be confident.
    assert max(item.confidence for item in profile.concepts) < 0.6


async def test_case_g_aggregates_one_concept_across_three_domains(
    db_session: AsyncSession, evaluation
) -> None:
    evidence = await concept_evidence_for(
        db_session, evaluation["G"].user_id, "science-fiction"
    )

    domains = {item.domain_slug for item in evidence.contributions}
    assert domains == {"anime", "literature", "manhwa"}
    assert evidence.direction == DIRECTION_POSITIVE
    # The signal is concept-level: every domain contributed a rated work.
    assert all(item.rating is not None for item in evidence.contributions)


async def test_case_h_normalization_is_specific_to_the_user(
    db_session: AsyncSession, evaluation
) -> None:
    """A 7 is this user's best; for case A it would be below average."""
    h_profile = await build_preference_profile(db_session, evaluation["H"].user_id)
    a_profile = await build_preference_profile(db_session, evaluation["A"].user_id)

    assert h_profile.rating_context.baseline < a_profile.rating_context.baseline

    h_sci_fi = evidence_by_slug(h_profile)["science-fiction"]
    assert h_sci_fi.direction == DIRECTION_POSITIVE
    assert max(h_sci_fi.ratings) == 7

    # And their low-rated works read negative, in the same profile.
    negatives = [
        item
        for item in h_profile.concepts
        if item.direction == DIRECTION_NEGATIVE and item.works_rated >= 2
    ]
    assert negatives


async def test_case_i_evidence_survives_removal_from_the_library(
    db_session: AsyncSession, evaluation
) -> None:
    evidence = await concept_evidence_for(
        db_session, evaluation["I"].user_id, "science-fiction"
    )

    assert evidence.works_removed > 0
    assert all(item.in_library is False for item in evidence.contributions)
    # The ratings they gave are still the evidence they gave.
    assert evidence.direction == DIRECTION_POSITIVE
    assert sorted(evidence.ratings) == [9, 10]


# =========================================================================
# Anti-leakage: deliberate attempts to make consumption look like preference
# =========================================================================


async def test_volume_of_unrated_completions_never_becomes_positive_preference(
    db_session: AsyncSession,
) -> None:
    """Twelve unrated completions must still give no preference direction."""
    await ensure_vocabulary(db_session)
    user = await make_user(db_session, "volume")
    for index in range(12):
        work = await make_work(db_session, 100 + index, ["Psychological"])
        await record(db_session, user, work)

    evidence = await concept_evidence_for(db_session, user.id, "psychological-depth")

    assert evidence.works_completed == 12
    assert evidence.exposure > 0.7  # consumption is loudly visible
    assert evidence.preference_evidence is None
    assert evidence.direction == DIRECTION_UNKNOWN
    assert evidence.confidence == 0.0


async def test_completing_low_rated_works_does_not_rescue_them(
    db_session: AsyncSession,
) -> None:
    """Finishing something you rated 2 is not evidence you liked it."""
    await ensure_vocabulary(db_session)
    user = await make_user(db_session, "lowrated")
    for index, rating in enumerate((2, 3, 2, 3, 2)):
        work = await make_work(db_session, 200 + index, ["Psychological"])
        await record(db_session, user, work, rating=rating)

    evidence = await concept_evidence_for(db_session, user.id, "psychological-depth")

    assert evidence.works_completed == 5
    assert evidence.engagement > 0
    assert evidence.direction == DIRECTION_NEGATIVE
    assert evidence.preference_evidence < 0


async def test_explicit_ratings_outweigh_equivalent_unrated_exposure(
    db_session: AsyncSession,
) -> None:
    """Two users, same consumption; only one said what they thought."""
    await ensure_vocabulary(db_session)
    rater = await make_user(db_session, "rater")
    silent = await make_user(db_session, "silent")
    works = [await make_work(db_session, 300 + index, ["Psychological"]) for index in range(4)]

    for work in works:
        await record(db_session, rater, work, rating=9)
        await record(db_session, silent, work)

    rated = await concept_evidence_for(db_session, rater.id, "psychological-depth")
    unrated = await concept_evidence_for(db_session, silent.id, "psychological-depth")

    assert rated.exposure == unrated.exposure
    assert rated.engagement == unrated.engagement
    assert rated.confidence > unrated.confidence
    assert rated.direction == DIRECTION_POSITIVE
    assert unrated.direction == DIRECTION_UNKNOWN


async def test_abandonment_is_not_a_hidden_low_rating(db_session: AsyncSession) -> None:
    """An abandoned work and a 2/10 must not read the same."""
    await ensure_vocabulary(db_session)
    quitter = await make_user(db_session, "quitter")
    disliker = await make_user(db_session, "disliker")

    for index in range(3):
        abandoned = await make_work(db_session, 400 + index, ["Psychological"])
        await record(
            db_session, quitter, abandoned, statuses=("in_progress", STATUS_ABANDONED)
        )
        rated_low = await make_work(db_session, 450 + index, ["Psychological"])
        await record(db_session, disliker, rated_low, rating=2)

    abandoned_evidence = await concept_evidence_for(
        db_session, quitter.id, "psychological-depth"
    )
    disliked_evidence = await concept_evidence_for(
        db_session, disliker.id, "psychological-depth"
    )

    assert abandoned_evidence.abandonment_signal > 0
    assert abandoned_evidence.direction == DIRECTION_UNKNOWN
    assert abandoned_evidence.preference_evidence is None

    assert disliked_evidence.direction == DIRECTION_NEGATIVE
    assert disliked_evidence.preference_evidence < 0


async def test_on_hold_is_not_counted_as_abandonment(db_session: AsyncSession) -> None:
    await ensure_vocabulary(db_session)
    user = await make_user(db_session, "paused")
    work = await make_work(db_session, 500, ["Psychological"])
    await record(db_session, user, work, statuses=("in_progress", STATUS_ON_HOLD))

    evidence = await concept_evidence_for(db_session, user.id, "psychological-depth")

    assert evidence.works_on_hold == 1
    assert evidence.works_abandoned == 0
    assert evidence.abandonment_signal == 0.0


async def test_reconsumption_does_not_inflate_the_rating_signal(
    db_session: AsyncSession,
) -> None:
    """Re-reading something rated 4 means engagement, not approval."""
    await ensure_vocabulary(db_session)
    user = await make_user(db_session, "rereader")
    work = await make_work(db_session, 600, ["Psychological"])
    await record(db_session, user, work, rating=4, repeats=3)

    evidence = await concept_evidence_for(db_session, user.id, "psychological-depth")

    assert evidence.total_completions == 4
    assert evidence.reconsumption_signal > 0
    # The rating channel is untouched by how often they went back.
    assert evidence.rating_signal < 0
    assert evidence.direction == DIRECTION_NEGATIVE


async def test_concept_density_does_not_inflate_per_concept_evidence(
    db_session: AsyncSession,
) -> None:
    """A work with eight labels contributes one work's worth to each.

    The Phase 1M asymmetry -- anime carries 13 concepts per work against
    literature's 1.5 -- must not turn into "prefers anime concepts".
    """
    await ensure_vocabulary(db_session)
    dense_user = await make_user(db_session, "dense")
    sparse_user = await make_user(db_session, "sparse")

    dense = await make_work(
        db_session,
        700,
        ["Psychological", "Crime", "War", "Tragedy", "Philosophy", "Mystery", "Revenge"],
    )
    sparse = await make_work(db_session, 701, ["Psychological"])

    await record(db_session, dense_user, dense, rating=9)
    await record(db_session, sparse_user, sparse, rating=9)

    dense_evidence = await concept_evidence_for(
        db_session, dense_user.id, "psychological-depth"
    )
    sparse_evidence = await concept_evidence_for(
        db_session, sparse_user.id, "psychological-depth"
    )

    assert dense_evidence.works_exposed == sparse_evidence.works_exposed == 1
    assert len(dense_evidence.contributions) == len(sparse_evidence.contributions) == 1
    assert dense_evidence.preference_evidence == sparse_evidence.preference_evidence
    assert dense_evidence.confidence == sparse_evidence.confidence

    # The dense work does reach more concepts -- which is true of the work.
    dense_profile = await build_preference_profile(db_session, dense_user.id)
    assert len(dense_profile.concepts) > 1


async def test_work_concept_confidence_is_not_the_users_confidence(
    db_session: AsyncSession,
) -> None:
    """A 0.95 content annotation does not make a 0.95 preference."""
    await ensure_vocabulary(db_session)
    user = await make_user(db_session, "annotated")
    work = await make_work(db_session, 800, ["Psychological"])
    await apply_source_labels(
        db_session, work=work, labels=[SourceLabel("Psychological", "anilist_tag", rank=95)]
    )
    await record(db_session, user, work, rating=9)

    evidence = await concept_evidence_for(db_session, user.id, "psychological-depth")
    contribution = evidence.contributions[0]

    assert contribution.concept_confidence == pytest.approx(0.95)
    # One rated work is weak evidence however certain the annotation is.
    assert evidence.confidence < 0.5


# =========================================================================
# Small data, attribution and isolation
# =========================================================================


async def test_a_user_with_no_interactions_gets_an_empty_profile(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, "newcomer")

    profile = await build_preference_profile(db_session, user.id)

    assert profile.concepts == []
    assert profile.total_interactions == 0
    assert profile.rating_context.rating_count == 0
    assert profile.rating_context.normalization_confidence == 0.0


async def test_one_rating_gives_a_direction_but_not_confidence(
    db_session: AsyncSession,
) -> None:
    """A single 10/10 must not produce a confident conclusion."""
    await ensure_vocabulary(db_session)
    user = await make_user(db_session, "oneshot")
    work = await make_work(db_session, 900, ["Psychological"])
    await record(db_session, user, work, rating=10)

    evidence = await concept_evidence_for(db_session, user.id, "psychological-depth")

    assert evidence.direction == DIRECTION_POSITIVE
    assert evidence.confidence < 0.4
    assert evidence.works_rated == 1


@pytest.mark.parametrize("count", [1, 2, 5, 20])
async def test_confidence_grows_with_agreeing_evidence(
    db_session: AsyncSession, count: int
) -> None:
    await ensure_vocabulary(db_session)
    user = await make_user(db_session, f"grow{count}")
    for index in range(count):
        work = await make_work(db_session, 1000 + count * 50 + index, ["Psychological"])
        await record(db_session, user, work, rating=9)

    evidence = await concept_evidence_for(db_session, user.id, "psychological-depth")

    assert evidence.works_rated == count
    assert evidence.direction == DIRECTION_POSITIVE
    assert 0.0 < evidence.confidence < 1.0


async def test_disagreeing_ratings_lower_confidence_below_agreeing_ones(
    db_session: AsyncSession,
) -> None:
    await ensure_vocabulary(db_session)
    agreeing = await make_user(db_session, "agreeing")
    conflicted = await make_user(db_session, "conflicted")

    for index, (steady, mixed) in enumerate(((9, 10), (9, 1), (9, 10), (9, 1))):
        steady_work = await make_work(db_session, 2000 + index, ["Psychological"])
        mixed_work = await make_work(db_session, 2100 + index, ["Psychological"])
        await record(db_session, agreeing, steady_work, rating=steady)
        await record(db_session, conflicted, mixed_work, rating=mixed)

    steady_evidence = await concept_evidence_for(
        db_session, agreeing.id, "psychological-depth"
    )
    mixed_evidence = await concept_evidence_for(
        db_session, conflicted.id, "psychological-depth"
    )

    assert steady_evidence.works_rated == mixed_evidence.works_rated
    assert steady_evidence.confidence > mixed_evidence.confidence


async def test_every_signal_is_traceable_to_the_rows_behind_it(
    db_session: AsyncSession, evaluation
) -> None:
    """The explainability requirement, checked rather than assumed."""
    evidence = await concept_evidence_for(
        db_session, evaluation["A"].user_id, "psychological-depth"
    )

    assert len(evidence.contributions) == evidence.works_exposed
    assert sum(1 for item in evidence.contributions if item.rating is not None) == (
        evidence.works_rated
    )
    for contribution in evidence.contributions:
        assert isinstance(contribution.work_id, uuid.UUID)
        assert contribution.title
        assert contribution.domain_slug
        if contribution.rating is not None:
            assert contribution.normalized_rating is not None


async def test_recomputation_is_deterministic(
    db_session: AsyncSession, evaluation
) -> None:
    first = await build_preference_profile(db_session, evaluation["A"].user_id)
    second = await build_preference_profile(db_session, evaluation["A"].user_id)

    assert [
        (item.concept_slug, item.preference_evidence, item.confidence) for item in first.concepts
    ] == [
        (item.concept_slug, item.preference_evidence, item.confidence)
        for item in second.concepts
    ]


async def test_one_users_evidence_never_includes_anothers(
    db_session: AsyncSession, evaluation
) -> None:
    a_profile = await build_preference_profile(db_session, evaluation["A"].user_id)
    b_profile = await build_preference_profile(db_session, evaluation["B"].user_id)

    assert a_profile.user_id != b_profile.user_id
    a_evidence = evidence_by_slug(a_profile)["psychological-depth"]
    b_evidence = evidence_by_slug(b_profile)["psychological-depth"]

    # Same works, entirely separate readings.
    assert {item.work_id for item in a_evidence.contributions} == {
        item.work_id for item in b_evidence.contributions
    }
    assert a_evidence.ratings != b_evidence.ratings
    assert a_evidence.preference_evidence != b_evidence.preference_evidence


async def test_a_work_with_no_concepts_is_reported_not_dropped(
    db_session: AsyncSession,
) -> None:
    """A content coverage gap must not look like an absence of interest."""
    await ensure_vocabulary(db_session)
    user = await make_user(db_session, "gap")
    work = await make_work(db_session, 3000, [])
    await record(db_session, user, work, rating=9)

    profile = await build_preference_profile(db_session, user.id)

    assert profile.total_interactions == 1
    assert profile.interactions_without_concepts == 1
    assert profile.concepts == []
