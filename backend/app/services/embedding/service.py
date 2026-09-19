"""Generating and storing ContentUnit embeddings.

Eligibility is deliberately narrow: an embedding is built from the actual
text of a ContentUnit and nothing else. Work descriptions, genres, provenance
fields, source URLs and relationship labels are metadata, not text of the
work, and embedding them would quietly mix description with content.

Both tiers are eligible -- literature `primary` prose and anime `summary`
text -- because both are real text. `text_tier` stays on the ContentUnit so
every later query can say which it meant.

Idempotency and staleness are decided from stored state, with no cache
layer: a vector is current when its source hash, model, revision and
preparation version all still match. Anything else is regenerated.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models import Container, ContentUnit, Domain, Embedding, Work
from app.services.embedding.encoder import Encoder
from app.services.embedding.preparation import (
    PREP_VERSION,
    EmptyTextError,
    prepare_text,
    text_hash,
)

OWNER_TYPE_CONTENT_UNIT = "content_unit"


@dataclass
class EmbeddingRunReport:
    eligible: int = 0
    generated: int = 0
    skipped_current: int = 0
    regenerated_stale: int = 0
    failed: list[dict] = field(default_factory=list)
    truncated: list[dict] = field(default_factory=list)
    model_name: str | None = None
    model_revision: str | None = None
    dimension: int | None = None


def is_stale(embedding: Embedding, *, source_hash: str, encoder: Encoder) -> bool:
    """True when a stored vector no longer represents its source.

    Four independent reasons, all of which must be checked: the text was
    edited, the model changed, the model's build changed, or the text
    preparation changed. Reusing a vector across any of them would be
    serving an answer computed from something else.
    """
    return (
        embedding.source_hash != source_hash
        or embedding.model_name != encoder.model_name
        or embedding.model_revision != encoder.model_revision
        or embedding.prep_version != PREP_VERSION
        or embedding.dimension != encoder.dimension
    )


def eligible_units_query(
    *,
    domain_slug: str | None = None,
    text_tier: str | None = None,
    work_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> Select:
    """ContentUnits with actual text, optionally narrowed by domain, tier or work.

    Only the unit's own text is eligible. Work descriptions, genres, tags,
    provenance and source URLs are metadata about a work, not text of it.

    `work_id` narrows to a single work, mirroring the filter semantic search
    already offers. It is what lets a caller re-embed just the work it has
    touched instead of walking the whole corpus.
    """
    query = (
        select(ContentUnit)
        .join(Container, ContentUnit.container_id == Container.id)
        .join(Work, Container.work_id == Work.id)
        .join(Domain, Work.domain_id == Domain.id)
        .where(ContentUnit.text_content.isnot(None), ContentUnit.text_content != "")
        .order_by(ContentUnit.id)
    )
    if domain_slug:
        query = query.where(Domain.slug == domain_slug)
    if text_tier:
        query = query.where(ContentUnit.text_tier == text_tier)
    if work_id:
        query = query.where(Work.id == work_id)
    if limit:
        query = query.limit(limit)
    return query


def eligible_content_units(
    session: Session,
    *,
    domain_slug: str | None = None,
    text_tier: str | None = None,
    work_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> list[ContentUnit]:
    query = eligible_units_query(
        domain_slug=domain_slug, text_tier=text_tier, work_id=work_id, limit=limit
    )
    return list(session.execute(query).scalars().all())


def _existing_embeddings(session: Session, unit_ids: list) -> dict:
    if not unit_ids:
        return {}
    rows = (
        session.execute(
            select(Embedding).where(
                Embedding.owner_type == OWNER_TYPE_CONTENT_UNIT,
                Embedding.owner_id.in_(unit_ids),
            )
        )
        .scalars()
        .all()
    )
    return {row.owner_id: row for row in rows}


def embed_content_units(
    session: Session,
    encoder: Encoder,
    *,
    units: list[ContentUnit],
    force: bool = False,
    batch_size: int = 32,
) -> EmbeddingRunReport:
    """Embed the given units, skipping any whose vector is already current."""
    report = EmbeddingRunReport(
        eligible=len(units),
        model_name=encoder.model_name,
        model_revision=encoder.model_revision,
        dimension=encoder.dimension,
    )

    existing = _existing_embeddings(session, [unit.id for unit in units])

    pending: list[tuple[ContentUnit, str, str, Embedding | None]] = []
    for unit in units:
        try:
            prepared = prepare_text(unit.text_content)
        except EmptyTextError as exc:
            report.failed.append({"content_unit_id": str(unit.id), "reason": str(exc)})
            continue

        digest = text_hash(prepared)
        current = existing.get(unit.id)
        if current is not None and not force and not is_stale(
            current, source_hash=digest, encoder=encoder
        ):
            report.skipped_current += 1
            continue

        pending.append((unit, prepared, digest, current))

    # One model load, then batches -- never a load per unit.
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        try:
            vectors = encoder.encode([prepared for _, prepared, _, _ in batch])
        except Exception as exc:  # noqa: BLE001 - reported per batch, not swallowed
            for unit, _, _, _ in batch:
                report.failed.append(
                    {"content_unit_id": str(unit.id), "reason": f"encode failed: {exc}"}
                )
            continue

        for (unit, prepared, digest, current), vector in zip(batch, vectors):
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
                    Embedding(
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
                current.model_name = encoder.model_name
                current.model_revision = encoder.model_revision
                current.source_hash = digest
                current.dimension = encoder.dimension
                current.normalized = encoder.normalized
                current.prep_version = PREP_VERSION
                report.regenerated_stale += 1

    session.flush()
    return report


def record_truncation(
    report: EmbeddingRunReport, encoder: Encoder, units: list[ContentUnit]
) -> None:
    """Note units longer than the model's window, which is lossy by definition.

    The model silently truncates past `max_seq_length`; recording which units
    that affects keeps a known limitation visible instead of invisible.
    """
    try:
        limit = encoder.max_sequence_length
    except Exception:  # noqa: BLE001 - diagnostics must never break a run
        return

    for unit in units:
        try:
            prepared = prepare_text(unit.text_content)
        except EmptyTextError:
            continue
        tokens = encoder.count_tokens(prepared)
        if tokens > limit:
            report.truncated.append(
                {
                    "content_unit_id": str(unit.id),
                    "tokens": tokens,
                    "limit": limit,
                    "text_tier": unit.text_tier,
                }
            )
