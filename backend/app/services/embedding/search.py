"""Vector similarity search over ContentUnit embeddings.

What this returns is **semantic similarity**: a distance between vectors
produced by one model. It is a computational observation, not a factual
relationship, not a thematic claim, and not an interpretation. Results are
never written to `Relationship`, and callers should present them labelled as
similarity.

All filtering happens in SQL. Pulling 830 rows into Python and filtering
there would work today and stop working for no visible reason later.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    OWNER_TYPE_CONTEXTUAL_PASSAGE,
    Container,
    ContentUnit,
    ContextualPassage,
    Domain,
    Embedding,
    TextSource,
    Work,
)
from app.services.embedding.encoder import Encoder
from app.services.embedding.preparation import EmptyTextError, prepare_text
from app.services.embedding.service import OWNER_TYPE_CONTENT_UNIT


REPRESENTATION_CONTENT_UNIT = "content_unit"
REPRESENTATION_CONTEXTUAL_PASSAGE = "contextual_passage"
REPRESENTATIONS = (REPRESENTATION_CONTENT_UNIT, REPRESENTATION_CONTEXTUAL_PASSAGE)


@dataclass
class SearchHit:
    """One nearest neighbour, from one representation.

    `representation` says what was actually embedded. A contextual passage is
    derived from several ContentUnits and is not itself a source unit, so
    `source_unit_ids` is populated for those hits and callers must not
    present them as text the source contained.
    """

    similarity: float
    distance: float
    text_excerpt: str
    text_tier: str
    representation: str
    work_id: uuid.UUID
    work_title: str
    domain_slug: str
    container_id: uuid.UUID
    container_type: str
    container_title: str | None
    container_sequence_number: int
    # Set for content_unit hits only.
    content_unit_id: uuid.UUID | None = None
    unit_type: str | None = None
    sequence_number: int | None = None
    source_name: str | None = None
    source_url: str | None = None
    licence: str | None = None
    # Set for contextual_passage hits only: the units that produced it.
    passage_id: uuid.UUID | None = None
    source_unit_ids: list[str] | None = None
    unit_count: int | None = None
    first_unit_sequence: int | None = None
    last_unit_sequence: int | None = None
    grouping_config: str | None = None


class EmptyQueryError(ValueError):
    """The query had no text to embed."""


def _excerpt(text: str | None, limit: int = 400) -> str:
    if not text:
        return ""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1].rstrip() + "…"


def build_search_query(
    query_vector: list[float],
    *,
    model_name: str,
    top_k: int,
    domain_slug: str | None = None,
    text_tier: str | None = None,
    work_id: uuid.UUID | None = None,
    container_id: uuid.UUID | None = None,
    grouping_config: str | None = None,
) -> Select:
    """Nearest neighbours over ContentUnit embeddings.

    Restricted to one model: vectors from different models are not
    comparable, so mixing them would produce meaningless distances.
    """
    distance = Embedding.vector.cosine_distance(query_vector).label("distance")

    statement = (
        select(ContentUnit, Work, Container, Domain.slug, TextSource, distance)
        .join(Embedding, Embedding.owner_id == ContentUnit.id)
        .join(Container, ContentUnit.container_id == Container.id)
        .join(Work, Container.work_id == Work.id)
        .join(Domain, Work.domain_id == Domain.id)
        .outerjoin(TextSource, ContentUnit.text_source_id == TextSource.id)
        .where(
            Embedding.owner_type == OWNER_TYPE_CONTENT_UNIT,
            Embedding.model_name == model_name,
        )
        .order_by(distance)
        .limit(top_k)
    )

    if domain_slug:
        statement = statement.where(Domain.slug == domain_slug)
    if text_tier:
        statement = statement.where(ContentUnit.text_tier == text_tier)
    if work_id:
        statement = statement.where(Work.id == work_id)
    if container_id:
        statement = statement.where(Container.id == container_id)

    return statement


def build_passage_search_query(
    query_vector: list[float],
    *,
    model_name: str,
    top_k: int,
    domain_slug: str | None = None,
    text_tier: str | None = None,
    work_id: uuid.UUID | None = None,
    container_id: uuid.UUID | None = None,
    grouping_config: str | None = None,
) -> Select:
    """Nearest neighbours over contextual passage embeddings.

    Deliberately a separate query rather than a union: the two
    representations are never mixed in one result set, because their
    distances describe different things.
    """
    distance = Embedding.vector.cosine_distance(query_vector).label("distance")

    statement = (
        select(ContextualPassage, Work, Container, Domain.slug, distance)
        .join(Embedding, Embedding.owner_id == ContextualPassage.id)
        .join(Container, ContextualPassage.container_id == Container.id)
        .join(Work, Container.work_id == Work.id)
        .join(Domain, Work.domain_id == Domain.id)
        .where(
            Embedding.owner_type == OWNER_TYPE_CONTEXTUAL_PASSAGE,
            Embedding.model_name == model_name,
        )
        .order_by(distance)
        .limit(top_k)
    )

    if domain_slug:
        statement = statement.where(Domain.slug == domain_slug)
    if text_tier:
        statement = statement.where(ContextualPassage.text_tier == text_tier)
    if work_id:
        statement = statement.where(Work.id == work_id)
    if container_id:
        statement = statement.where(Container.id == container_id)
    if grouping_config:
        statement = statement.where(ContextualPassage.grouping_config == grouping_config)

    return statement


async def semantic_search(
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
) -> list[SearchHit]:
    """Embed the query with the same model, then search pgvector.

    `representation` selects which embedded form to search. The two are
    never blended: a score against a 34-token fragment and a score against a
    three-paragraph passage are not the same measurement.
    """
    if representation not in REPRESENTATIONS:
        raise ValueError(f"unknown representation {representation!r}")

    try:
        prepared = prepare_text(query)
    except EmptyTextError as exc:
        raise EmptyQueryError(str(exc)) from exc

    vectors = encoder.encode([prepared])
    if not vectors:
        raise EmptyQueryError("the encoder returned no vector for the query")

    filters = dict(
        model_name=encoder.model_name,
        top_k=top_k,
        domain_slug=domain_slug,
        text_tier=text_tier,
        work_id=work_id,
        container_id=container_id,
        grouping_config=grouping_config,
    )

    if representation == REPRESENTATION_CONTEXTUAL_PASSAGE:
        rows = (await session.execute(build_passage_search_query(vectors[0], **filters))).all()
        return [
            SearchHit(
                similarity=round(1.0 - float(distance), 6),
                distance=round(float(distance), 6),
                text_excerpt=_excerpt(passage.text_content),
                text_tier=passage.text_tier,
                representation=REPRESENTATION_CONTEXTUAL_PASSAGE,
                work_id=work.id,
                work_title=work.title,
                domain_slug=domain,
                container_id=container.id,
                container_type=container.container_type,
                container_title=container.title,
                container_sequence_number=container.sequence_number,
                passage_id=passage.id,
                source_unit_ids=list(passage.source_unit_ids or []),
                unit_count=passage.unit_count,
                first_unit_sequence=passage.first_unit_sequence,
                last_unit_sequence=passage.last_unit_sequence,
                grouping_config=passage.grouping_config,
            )
            for passage, work, container, domain, distance in rows
        ]

    rows = (await session.execute(build_search_query(vectors[0], **filters))).all()
    return [
        SearchHit(
            # Vectors are L2-normalized, so cosine distance is in [0, 2] and
            # this reads as the familiar cosine similarity.
            similarity=round(1.0 - float(distance), 6),
            distance=round(float(distance), 6),
            text_excerpt=_excerpt(unit.text_content),
            text_tier=unit.text_tier,
            representation=REPRESENTATION_CONTENT_UNIT,
            work_id=work.id,
            work_title=work.title,
            domain_slug=domain,
            container_id=container.id,
            container_type=container.container_type,
            container_title=container.title,
            container_sequence_number=container.sequence_number,
            content_unit_id=unit.id,
            unit_type=unit.unit_type,
            sequence_number=unit.sequence_number,
            source_name=text_source.source_name if text_source else None,
            source_url=text_source.source_url if text_source else None,
            licence=text_source.licence if text_source else None,
        )
        for unit, work, container, domain, text_source, distance in rows
    ]
