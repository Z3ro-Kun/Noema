"""Phase 1AC: semantic search answered in works.

The retrieval underneath is unchanged and still tested where it lives. What
is under test here is the fold that sits on top of it, and the one thing it
has to get right: a novel that matches in four places is one result.

    a work appears once      however many of its passages matched

    top_k counts works       ten results means ten works, which is why the
                             candidate pool is wider than the answer

    the score is a score     the strongest underlying similarity, unchanged,
                             traceable to one passage -- never a mean, a sum,
                             a count or anything weighted

    order is stable          similarity, then work id, so two identical
                             requests cannot disagree

The folding rules are pure, so most of this needs neither a database nor a
model. The endpoint tests below cover the parts that do.
"""

import uuid

import pytest

from app.services.embedding.search import (
    REPRESENTATION_CONTENT_UNIT,
    REPRESENTATION_CONTEXTUAL_PASSAGE,
    SearchHit,
)
from app.services.embedding.work_search import (
    CANDIDATE_CEILING,
    CANDIDATE_MULTIPLIER,
    EVIDENCE_CHARS,
    aggregate_by_work,
    candidate_pool_size,
)

WORK_A = uuid.UUID("00000000-0000-4000-8000-00000000000a")
WORK_B = uuid.UUID("00000000-0000-4000-8000-00000000000b")
WORK_C = uuid.UUID("00000000-0000-4000-8000-00000000000c")


def hit(
    work_id: uuid.UUID,
    similarity: float,
    *,
    title: str = "A Work",
    excerpt: str = "some text",
    representation: str = REPRESENTATION_CONTENT_UNIT,
    text_tier: str = "primary",
    container_sequence_number: int = 1,
) -> SearchHit:
    return SearchHit(
        similarity=similarity,
        distance=round(1.0 - similarity, 6),
        text_excerpt=excerpt,
        text_tier=text_tier,
        representation=representation,
        work_id=work_id,
        work_title=title,
        domain_slug="literature",
        container_id=uuid.uuid4(),
        container_type="chapter",
        container_title="A Chapter",
        container_sequence_number=container_sequence_number,
    )


# --- the fold --------------------------------------------------------------


def test_many_passages_of_one_work_become_one_result() -> None:
    """The reason this layer exists."""
    matches = aggregate_by_work(
        [hit(WORK_A, 0.62), hit(WORK_A, 0.58), hit(WORK_A, 0.55), hit(WORK_B, 0.51)],
        top_k=10,
    )

    assert [match.work_id for match in matches] == [WORK_A, WORK_B]
    assert len(matches) == 2


def test_the_strongest_passage_gives_the_work_its_score() -> None:
    """Not a mean, not a sum, not weighted by how many matched."""
    matches = aggregate_by_work(
        [hit(WORK_A, 0.31), hit(WORK_A, 0.77), hit(WORK_A, 0.40)], top_k=10
    )

    assert matches[0].similarity == 0.77
    assert matches[0].distance == pytest.approx(1.0 - 0.77, abs=1e-6)


def test_the_kept_evidence_is_the_strongest_hit() -> None:
    matches = aggregate_by_work(
        [
            hit(WORK_A, 0.31, excerpt="a weaker passage", container_sequence_number=4),
            hit(WORK_A, 0.77, excerpt="the strongest passage", container_sequence_number=9),
        ],
        top_k=10,
    )

    assert matches[0].excerpt == "the strongest passage"
    assert matches[0].container_sequence_number == 9


def test_how_many_passages_matched_is_reported_not_scored() -> None:
    matches = aggregate_by_work(
        [hit(WORK_A, 0.40), hit(WORK_A, 0.39), hit(WORK_B, 0.60)], top_k=10
    )
    by_work = {match.work_id: match for match in matches}

    assert by_work[WORK_A].matching_passages == 2
    assert by_work[WORK_B].matching_passages == 1
    # The work with one matching passage still ranks first, because the count
    # is context and the similarity is the score.
    assert matches[0].work_id == WORK_B


def test_top_k_counts_works_not_passages() -> None:
    hits = [hit(WORK_A, 0.9), hit(WORK_A, 0.8), hit(WORK_B, 0.7), hit(WORK_C, 0.6)]

    assert len(aggregate_by_work(hits, top_k=2)) == 2
    assert [match.work_id for match in aggregate_by_work(hits, top_k=2)] == [WORK_A, WORK_B]


def test_results_are_ordered_by_similarity() -> None:
    matches = aggregate_by_work(
        [hit(WORK_A, 0.2), hit(WORK_B, 0.9), hit(WORK_C, 0.5)], top_k=10
    )

    assert [match.similarity for match in matches] == [0.9, 0.5, 0.2]


def test_a_tie_is_broken_deterministically() -> None:
    """Two identical requests must not disagree about the order."""
    first = aggregate_by_work([hit(WORK_C, 0.5), hit(WORK_A, 0.5), hit(WORK_B, 0.5)], top_k=10)
    second = aggregate_by_work([hit(WORK_B, 0.5), hit(WORK_C, 0.5), hit(WORK_A, 0.5)], top_k=10)

    assert [match.work_id for match in first] == [WORK_A, WORK_B, WORK_C]
    assert [match.work_id for match in first] == [match.work_id for match in second]


def test_an_empty_pool_folds_to_nothing() -> None:
    assert aggregate_by_work([], top_k=10) == []


def test_the_evidence_excerpt_is_short_by_contract() -> None:
    """A search result points at the corpus; it is not a way to read it."""
    matches = aggregate_by_work([hit(WORK_A, 0.5, excerpt="x" * 5_000)], top_k=10)

    assert len(matches[0].excerpt) == EVIDENCE_CHARS


def test_the_representation_travels_with_the_hit() -> None:
    """The two are never blended, so a result says which one it is."""
    matches = aggregate_by_work(
        [hit(WORK_A, 0.5, representation=REPRESENTATION_CONTEXTUAL_PASSAGE)], top_k=10
    )

    assert matches[0].representation == REPRESENTATION_CONTEXTUAL_PASSAGE


def test_the_text_tier_travels_with_the_hit() -> None:
    """A match against a summary is not a match against the work's own words."""
    matches = aggregate_by_work([hit(WORK_A, 0.5, text_tier="summary")], top_k=10)

    assert matches[0].text_tier == "summary"


# --- the candidate pool ----------------------------------------------------


def test_the_pool_is_wider_than_the_answer() -> None:
    """Ten raw hits cannot yield ten works when duplicates are the problem."""
    assert candidate_pool_size(10) == 10 * CANDIDATE_MULTIPLIER
    assert candidate_pool_size(1) == CANDIDATE_MULTIPLIER


def test_the_pool_is_bounded() -> None:
    """Widening is not the same as an unbounded scan."""
    assert candidate_pool_size(100) == CANDIDATE_CEILING
    assert candidate_pool_size(10_000) == CANDIDATE_CEILING


def test_a_wide_pool_survives_early_duplicates() -> None:
    """The case the multiplier exists for.

    The first ten raw hits are all one work. Asking for ten hits would have
    returned one work; asking for sixty returns the three that are there.
    """
    hits = [hit(WORK_A, 0.9 - index * 0.01) for index in range(10)]
    hits += [hit(WORK_B, 0.5), hit(WORK_C, 0.4)]

    assert len(aggregate_by_work(hits[:10], top_k=3)) == 1
    assert len(aggregate_by_work(hits, top_k=3)) == 3
