"""Semantic search, answered in works rather than in paragraphs.

The retrieval underneath is unchanged: the same encoder, the same pgvector
query, the same representations, the same filters, the same provenance. This
module sits on top of `semantic_search` and answers a different question.

---

Why a layer rather than a different query

A nearest-neighbour search over content units returns the ten closest
*paragraphs*, and a novel that matches well tends to match many times. Ten
raw hits routinely turn out to be three works, so a product surface built on
them showed a reader the same book four times and called it four results.

So the raw hits stay exactly as they are -- the retrieval-inspection surface
still wants them, and they are what the vectors actually say -- and this
folds them into the works they came from:

    retrieve a wider candidate pool
        -> filters already applied, in SQL
    group by work_id
        -> strongest hit wins; the rest are counted, not shown
    sort by that similarity, then by work id
        -> deterministic
    take the first `top_k`

`top_k` therefore means what a reader assumes it means: that many different
works.

---

Candidate expansion

Asking for `top_k` raw hits cannot yield `top_k` works, because the duplicates
are the whole problem. So the pool is widened by a fixed multiplier, with a
ceiling so a large `top_k` cannot turn into an unbounded scan. The multiplier
is a guess about redundancy, not a tuned parameter, and being short of works
is a possible outcome rather than a failure: the corpus may simply not hold
that many works whose text is close to the query.

---

What this layer does not do

The work-level score is the strongest underlying similarity and nothing else.
No IDF, no rarity or novelty weighting, no aggregation across a work's hits,
no preference weighting, no personalisation, no composite ranking. A reader
who is told 0.61 can find the passage that scored 0.61. Blending the two
representations is likewise refused upstream and refused here: a score
against a 34-token fragment and a score against a three-paragraph passage are
not the same measurement.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.embedding.encoder import Encoder
from app.services.embedding.search import (
    REPRESENTATION_CONTENT_UNIT,
    SearchHit,
    semantic_search,
)

# How much wider the candidate pool is than the answer. Six is enough for a
# work that matches heavily to be represented several times without crowding
# every other work out of the pool.
CANDIDATE_MULTIPLIER = 6

# A ceiling, so `top_k=100` asks for 600 rather than something unbounded.
CANDIDATE_CEILING = 300

# The strongest supporting passage, quoted short. Long enough to recognise
# why a work surfaced, far too short to read the work from.
EVIDENCE_CHARS = 200


@dataclass(frozen=True)
class WorkMatch:
    """One work, and the strongest evidence that it matched.

    `similarity` is that single strongest hit's similarity -- never a mean, a
    sum or a count-weighted score, so it stays traceable to one passage a
    reader can be shown.
    """

    work_id: uuid.UUID
    work_title: str
    domain_slug: str
    similarity: float
    distance: float
    representation: str
    text_tier: str
    # How many candidate hits belonged to this work. Reported as context
    # for the match, never folded into the score.
    matching_passages: int
    # Where the strongest hit sits, so "why did this appear" is answerable.
    # All four are null when the strongest hit is a work-level summary: it
    # describes the whole work, so "where" is the work itself.
    container_id: uuid.UUID | None
    container_type: str | None
    container_title: str | None
    container_sequence_number: int | None
    excerpt: str
    source_name: str | None
    licence: str | None


def candidate_pool_size(top_k: int) -> int:
    """How many raw hits to ask for, to end up with `top_k` works."""
    return min(CANDIDATE_CEILING, max(top_k, top_k * CANDIDATE_MULTIPLIER))


def aggregate_by_work(hits: list[SearchHit], *, top_k: int) -> list[WorkMatch]:
    """Fold raw hits into works, strongest first.

    Pure and synchronous, so the folding rules can be tested without a
    database or a model. Ordering is by similarity descending and then by
    work id, which makes the result stable when two works tie -- an ordering
    that changes between identical requests is not a ranking.
    """
    strongest: dict[uuid.UUID, SearchHit] = {}
    counts: dict[uuid.UUID, int] = {}

    for hit in hits:
        counts[hit.work_id] = counts.get(hit.work_id, 0) + 1
        best = strongest.get(hit.work_id)
        # Strictly greater, so the first hit seen at a given similarity wins
        # and the order the database returned is preserved within a tie.
        if best is None or hit.similarity > best.similarity:
            strongest[hit.work_id] = hit

    matches = [
        WorkMatch(
            work_id=hit.work_id,
            work_title=hit.work_title,
            domain_slug=hit.domain_slug,
            similarity=hit.similarity,
            distance=hit.distance,
            representation=hit.representation,
            text_tier=hit.text_tier,
            matching_passages=counts[work_id],
            container_id=hit.container_id,
            container_type=hit.container_type,
            container_title=hit.container_title,
            container_sequence_number=hit.container_sequence_number,
            excerpt=(hit.text_excerpt or "")[:EVIDENCE_CHARS],
            source_name=hit.source_name,
            licence=hit.licence,
        )
        for work_id, hit in strongest.items()
    ]

    matches.sort(key=lambda match: (-match.similarity, str(match.work_id)))
    return matches[:top_k]


async def semantic_work_search(
    session: AsyncSession,
    encoder: Encoder,
    *,
    query: str,
    top_k: int = 10,
    domain_slug: str | None = None,
    text_tier: str | None = None,
    work_id: uuid.UUID | None = None,
    container_id: uuid.UUID | None = None,
    representation: str = REPRESENTATION_CONTENT_UNIT,
    grouping_config: str | None = None,
) -> list[WorkMatch]:
    """`top_k` unique works, most similar first.

    Every filter is passed straight through to `semantic_search`, which
    applies them in SQL -- so filtering happens before aggregation and a
    domain filter narrows what is *searched* rather than what survives the
    fold.
    """
    hits = await semantic_search(
        session,
        encoder,
        query=query,
        top_k=candidate_pool_size(top_k),
        domain_slug=domain_slug,
        text_tier=text_tier,
        work_id=work_id,
        container_id=container_id,
        representation=representation,
        grouping_config=grouping_config,
    )
    return aggregate_by_work(hits, top_k=top_k)


__all__ = [
    "CANDIDATE_CEILING",
    "CANDIDATE_MULTIPLIER",
    "EVIDENCE_CHARS",
    "WorkMatch",
    "aggregate_by_work",
    "candidate_pool_size",
    "semantic_work_search",
]
