"""Evaluating a candidate embedding model against the production baseline.

Everything except the model is held fixed: same corpus, same ContentUnit
representation, same text preparation, same cosine metric, same filters,
same top-k. The model is the only variable, which is what makes the
comparison mean anything.

Candidate vectors go to `experiment_embeddings`; production `embeddings` are
never written here. Running an experiment changes nothing about what the
application serves.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models import (
    Container,
    ContentUnit,
    Domain,
    ExperimentEmbedding,
    Work,
)
from app.services.embedding.encoder import Encoder
from app.services.embedding.preparation import (
    PREP_VERSION,
    EmptyTextError,
    prepare_text,
    text_hash,
)
from app.services.embedding.service import OWNER_TYPE_CONTENT_UNIT, eligible_units_query


@dataclass
class CandidateRunReport:
    model_name: str
    dimension: int
    model_revision: str | None = None
    eligible: int = 0
    generated: int = 0
    skipped_current: int = 0
    regenerated: int = 0
    failed: list[dict] = field(default_factory=list)


@dataclass
class CandidateHit:
    content_unit_id: uuid.UUID
    similarity: float
    text_excerpt: str
    text_tier: str
    work_title: str
    domain_slug: str
    container_title: str | None
    container_type: str
    container_sequence_number: int


def _excerpt(text: str | None, limit: int = 400) -> str:
    if not text:
        return ""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1].rstrip() + "…"


def embed_with_candidate(
    session: Session,
    encoder: Encoder,
    *,
    domain_slug: str | None = None,
    text_tier: str | None = None,
    work_id: uuid.UUID | None = None,
    force: bool = False,
    batch_size: int = 16,
) -> CandidateRunReport:
    """Embed eligible ContentUnits with a candidate model.

    Batch size is smaller than the production default because a base-sized
    model uses considerably more memory per item than MiniLM.

    `work_id` narrows the run to one work, which is how a caller evaluates a
    candidate against a subset without re-encoding the whole corpus.
    """
    report = CandidateRunReport(
        model_name=encoder.model_name,
        dimension=encoder.dimension,
        model_revision=encoder.model_revision,
    )

    units = list(
        session.execute(
            eligible_units_query(
                domain_slug=domain_slug, text_tier=text_tier, work_id=work_id
            )
        )
        .scalars()
        .all()
    )
    report.eligible = len(units)

    existing = {
        row.owner_id: row
        for row in session.execute(
            select(ExperimentEmbedding).where(
                ExperimentEmbedding.owner_type == OWNER_TYPE_CONTENT_UNIT,
                ExperimentEmbedding.model_name == encoder.model_name,
                ExperimentEmbedding.owner_id.in_([u.id for u in units]),
            )
        )
        .scalars()
        .all()
    } if units else {}

    pending = []
    for unit in units:
        try:
            prepared = prepare_text(unit.text_content)
        except EmptyTextError as exc:
            report.failed.append({"content_unit_id": str(unit.id), "reason": str(exc)})
            continue

        digest = text_hash(prepared)
        current = existing.get(unit.id)
        if current is not None and not force and current.source_hash == digest:
            report.skipped_current += 1
            continue
        pending.append((unit, prepared, digest, current))

    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        try:
            vectors = encoder.encode([prepared for _, prepared, _, _ in batch])
        except Exception as exc:  # noqa: BLE001 - reported per batch
            for unit, _, _, _ in batch:
                report.failed.append(
                    {"content_unit_id": str(unit.id), "reason": f"encode failed: {exc}"}
                )
            continue

        for (unit, _, digest, current), vector in zip(batch, vectors):
            if len(vector) != encoder.dimension:
                report.failed.append(
                    {
                        "content_unit_id": str(unit.id),
                        "reason": f"expected {encoder.dimension} dims, got {len(vector)}",
                    }
                )
                continue

            if current is None:
                session.add(
                    ExperimentEmbedding(
                        owner_type=OWNER_TYPE_CONTENT_UNIT,
                        owner_id=unit.id,
                        model_name=encoder.model_name,
                        model_revision=encoder.model_revision,
                        vector=vector,
                        source_hash=digest,
                        dimension=encoder.dimension,
                        normalized=encoder.normalized,
                        prep_version=PREP_VERSION,
                    )
                )
                report.generated += 1
            else:
                current.vector = vector
                current.source_hash = digest
                current.model_revision = encoder.model_revision
                current.dimension = encoder.dimension
                report.regenerated += 1

    session.flush()
    return report


def build_candidate_search_query(
    query_vector: list[float],
    *,
    model_name: str,
    top_k: int,
    domain_slug: str | None = None,
    text_tier: str | None = None,
    work_id: uuid.UUID | None = None,
) -> Select:
    """Nearest neighbours among one candidate model's vectors.

    The `model_name` filter is mandatory, not optional: this table holds
    vectors of differing widths, and comparing across widths is an error
    rather than a wrong answer.
    """
    distance = ExperimentEmbedding.vector.cosine_distance(query_vector).label("distance")

    statement = (
        select(ContentUnit, Work, Container, Domain.slug, distance)
        .join(ExperimentEmbedding, ExperimentEmbedding.owner_id == ContentUnit.id)
        .join(Container, ContentUnit.container_id == Container.id)
        .join(Work, Container.work_id == Work.id)
        .join(Domain, Work.domain_id == Domain.id)
        .where(
            ExperimentEmbedding.owner_type == OWNER_TYPE_CONTENT_UNIT,
            ExperimentEmbedding.model_name == model_name,
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

    return statement


def candidate_search(
    session: Session,
    encoder: Encoder,
    *,
    query: str,
    top_k: int = 5,
    domain_slug: str | None = None,
    text_tier: str | None = None,
    work_id: uuid.UUID | None = None,
) -> list[CandidateHit]:
    """Same retrieval procedure as production, against candidate vectors."""
    prepared = prepare_text(query)
    vectors = encoder.encode([prepared])

    rows = session.execute(
        build_candidate_search_query(
            vectors[0],
            model_name=encoder.model_name,
            top_k=top_k,
            domain_slug=domain_slug,
            text_tier=text_tier,
            work_id=work_id,
        )
    ).all()

    return [
        CandidateHit(
            content_unit_id=unit.id,
            similarity=round(1.0 - float(distance), 6),
            text_excerpt=_excerpt(unit.text_content),
            text_tier=unit.text_tier,
            work_title=work.title,
            domain_slug=domain,
            container_title=container.title,
            container_type=container.container_type,
            container_sequence_number=container.sequence_number,
        )
        for unit, work, container, domain, distance in rows
    ]
