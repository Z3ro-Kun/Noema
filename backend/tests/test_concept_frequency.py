"""Corpus concept frequency and the inverse-frequency weight.

Pure functions plus two aggregate queries. The properties under test are the
ones the Phase 1Q experiment depends on: the weight is corpus-level, it is
bounded, it never erases a common concept, and it never lets a single-work
concept run away.
"""

import math

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preference.frequency import (
    FrequencyParameters,
    build_frequencies,
    idf,
    load_corpus_frequencies,
)


# --- the weight itself -----------------------------------------------------


def test_rarer_concepts_weigh_more() -> None:
    values = [idf(df, 17) for df in (1, 3, 8, 11, 17)]

    assert values == sorted(values, reverse=True)


def test_a_concept_on_every_work_is_damped_not_erased() -> None:
    """Being common is not disqualifying -- the whole caution of this phase."""
    assert idf(17, 17) == pytest.approx(1.0)
    assert idf(17, 17) > 0


def test_smoothing_damps_the_rare_end() -> None:
    """With 17 works, df=1 against df=2 is one work, which is noise."""
    sharp = idf(1, 17, FrequencyParameters(smoothing=1.0)) / idf(
        2, 17, FrequencyParameters(smoothing=1.0)
    )
    smooth = idf(1, 17, FrequencyParameters(smoothing=5.0)) / idf(
        2, 17, FrequencyParameters(smoothing=5.0)
    )

    assert smooth < sharp


def test_the_formula_is_the_documented_one() -> None:
    assert idf(3, 17, FrequencyParameters(smoothing=2.0)) == pytest.approx(
        math.log((17 + 2) / (3 + 2)) + 1
    )


# --- specificity: bounded, and reachable at both ends ----------------------


def test_specificity_is_bounded_and_peaks_at_the_rarest_attainable_concept() -> None:
    frequencies = build_frequencies({"rare": 1, "mid": 8, "everywhere": 17}, 17)

    assert frequencies.by_slug["rare"].specificity == pytest.approx(1.0)
    assert 0.0 < frequencies.by_slug["everywhere"].specificity < 1.0
    for entry in frequencies.by_slug.values():
        assert 0.0 < entry.specificity <= 1.0


def test_a_ubiquitous_concept_keeps_a_meaningful_share_of_weight() -> None:
    """Not a rounding artifact: it must stay large enough to compete."""
    frequencies = build_frequencies({"everywhere": 17, "rare": 1}, 17)

    assert frequencies.by_slug["everywhere"].specificity > 0.3


def test_specificity_is_monotonic_in_frequency() -> None:
    counts = {f"c{df}": df for df in range(1, 18)}
    frequencies = build_frequencies(counts, 17)

    values = [frequencies.by_slug[f"c{df}"].specificity for df in range(1, 18)]
    assert values == sorted(values, reverse=True)


@pytest.mark.parametrize("smoothing", [1.0, 2.0, 5.0])
def test_the_weight_range_stays_modest_under_every_smoothing(smoothing: float) -> None:
    """A single-work concept must not be able to run away.

    The spread between the rarest and the most common concept is what caps
    how far frequency alone can move a ranking. Keeping it near a factor of
    three means user evidence -- which spans a comparable range -- is never
    swamped by rarity.
    """
    parameters = FrequencyParameters(smoothing=smoothing)
    ratio = idf(1, 17, parameters) / idf(17, 17, parameters)

    assert 1.0 < ratio < 4.0


# --- it is a property of the corpus, never of a user -----------------------


async def test_frequencies_are_identical_regardless_of_who_asks(
    db_session: AsyncSession,
) -> None:
    """The load-bearing isolation property: no user activity leaks into idf."""
    from app.services import auth_service, library_service
    from sqlalchemy import select
    from app.models import Work

    first = await load_corpus_frequencies(db_session)

    busy = await auth_service.register_user(
        db_session, email="busy@frequency.test", password="a-sufficiently-long-password"
    )
    works = (await db_session.execute(select(Work).limit(5))).scalars().all()
    for work in works:
        await library_service.add_to_library(db_session, user_id=busy.id, work_id=work.id)
        await library_service.set_status(
            db_session, user_id=busy.id, work_id=work.id, status="completed"
        )
        await library_service.set_rating(
            db_session, user_id=busy.id, work_id=work.id, rating=10
        )

    second = await load_corpus_frequencies(db_session)

    assert first.total_works == second.total_works
    assert {slug: entry.document_frequency for slug, entry in first.by_slug.items()} == {
        slug: entry.document_frequency for slug, entry in second.by_slug.items()
    }


async def test_document_frequency_counts_distinct_works(
    db_session: AsyncSession,
) -> None:
    from sqlalchemy import func, select
    from app.models import Concept, WorkConcept

    frequencies = await load_corpus_frequencies(db_session)

    rows = (
        await db_session.execute(
            select(Concept.slug, func.count(func.distinct(WorkConcept.work_id)))
            .join(WorkConcept, WorkConcept.concept_id == Concept.id)
            .group_by(Concept.slug)
        )
    ).all()

    assert frequencies.by_slug
    for slug, count in rows:
        assert frequencies.by_slug[slug].document_frequency == count
        assert count <= frequencies.total_works
