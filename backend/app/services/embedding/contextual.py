"""Building contextual passages and embedding them.

Passages are derived, so they are rebuilt rather than migrated: if a
container's text or the grouping config changes, the affected passages are
replaced and their embeddings follow. Source ContentUnits are never touched.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import Select, delete, select
from sqlalchemy.orm import Session

from app.models import (
    OWNER_TYPE_CONTEXTUAL_PASSAGE,
    Container,
    ContentUnit,
    ContextualPassage,
    Domain,
    Embedding,
    Work,
)
from app.services.embedding.encoder import Encoder
from app.services.embedding.grouping import GroupingConfig, SourceUnit, build_passages
from app.services.embedding.preparation import PREP_VERSION
from app.services.embedding.service import EmbeddingRunReport, is_stale


@dataclass
class PassageBuildReport:
    containers_processed: int = 0
    passages_created: int = 0
    passages_unchanged: int = 0
    passages_removed: int = 0
    grouping_config: str = ""
    unit_counts: list[int] = field(default_factory=list)
    char_lengths: list[int] = field(default_factory=list)


def containers_query(
    domain_slug: str | None = None, work_id: uuid.UUID | None = None
) -> Select:
    query = (
        select(Container)
        .join(Work, Container.work_id == Work.id)
        .join(Domain, Work.domain_id == Domain.id)
        .order_by(Container.id)
    )
    if domain_slug:
        query = query.where(Domain.slug == domain_slug)
    if work_id:
        query = query.where(Work.id == work_id)
    return query


def build_passages_for_corpus(
    session: Session,
    count_tokens,
    *,
    domain_slug: str | None = None,
    work_id: uuid.UUID | None = None,
    config: GroupingConfig | None = None,
) -> PassageBuildReport:
    """Rebuild contextual passages for every container in scope.

    Idempotent: a container whose passages already match the current text and
    config is left alone, so re-running creates nothing and orphans nothing.

    `work_id` narrows the rebuild to one work, so a caller that changed a
    single work does not walk the entire corpus to find that out.
    """
    config = config or GroupingConfig()
    report = PassageBuildReport(grouping_config=config.key)

    containers = list(
        session.execute(containers_query(domain_slug, work_id)).scalars().all()
    )

    for container in containers:
        report.containers_processed += 1

        units = list(
            session.execute(
                select(ContentUnit)
                .where(ContentUnit.container_id == container.id)
                .order_by(ContentUnit.sequence_number, ContentUnit.id)
            )
            .scalars()
            .all()
        )
        drafts = build_passages(
            [
                SourceUnit(
                    id=unit.id,
                    sequence_number=unit.sequence_number,
                    text=unit.text_content or "",
                    text_tier=unit.text_tier,
                )
                for unit in units
            ],
            count_tokens,
            config,
        )

        existing = list(
            session.execute(
                select(ContextualPassage).where(
                    ContextualPassage.container_id == container.id,
                    ContextualPassage.grouping_config == config.key,
                )
            )
            .scalars()
            .all()
        )
        existing_by_seq = {passage.sequence_number: passage for passage in existing}
        draft_hashes = {draft.sequence_number: draft.source_hash for draft in drafts}

        # Unchanged when the same sequence numbers map to the same hashes.
        unchanged = {
            sequence
            for sequence, passage in existing_by_seq.items()
            if draft_hashes.get(sequence) == passage.source_hash
        }
        if len(unchanged) == len(drafts) == len(existing):
            report.passages_unchanged += len(drafts)
            report.unit_counts.extend(d.unit_count for d in drafts)
            report.char_lengths.extend(len(d.text_content) for d in drafts)
            continue

        # Otherwise replace this container's passages wholesale: partial
        # repair would leave stale sequence numbers behind for no benefit at
        # this corpus size.
        stale_ids = [passage.id for passage in existing]
        if stale_ids:
            session.execute(
                delete(Embedding).where(
                    Embedding.owner_type == OWNER_TYPE_CONTEXTUAL_PASSAGE,
                    Embedding.owner_id.in_(stale_ids),
                )
            )
            session.execute(
                delete(ContextualPassage).where(ContextualPassage.id.in_(stale_ids))
            )
            report.passages_removed += len(stale_ids)

        for draft in drafts:
            session.add(
                ContextualPassage(
                    container_id=container.id,
                    sequence_number=draft.sequence_number,
                    source_unit_ids=[str(unit_id) for unit_id in draft.source_unit_ids],
                    first_unit_sequence=draft.first_unit_sequence,
                    last_unit_sequence=draft.last_unit_sequence,
                    unit_count=draft.unit_count,
                    text_content=draft.text_content,
                    text_tier=draft.text_tier,
                    grouping_config=draft.grouping_config,
                    source_hash=draft.source_hash,
                )
            )
            report.passages_created += 1
            report.unit_counts.append(draft.unit_count)
            report.char_lengths.append(len(draft.text_content))

    session.flush()
    return report


def embed_passages(
    session: Session,
    encoder: Encoder,
    *,
    grouping_config: str,
    domain_slug: str | None = None,
    work_id: uuid.UUID | None = None,
    force: bool = False,
    batch_size: int = 32,
) -> EmbeddingRunReport:
    """Embed contextual passages, skipping any whose vector is current.

    Mirrors the ContentUnit embedding path exactly -- same staleness rules,
    same metadata -- so the two representations stay comparable.
    """
    query = (
        select(ContextualPassage)
        .join(Container, ContextualPassage.container_id == Container.id)
        .join(Work, Container.work_id == Work.id)
        .where(ContextualPassage.grouping_config == grouping_config)
        .order_by(ContextualPassage.id)
    )
    if domain_slug:
        query = query.join(Domain, Work.domain_id == Domain.id).where(
            Domain.slug == domain_slug
        )
    if work_id:
        query = query.where(Work.id == work_id)

    passages = list(session.execute(query).scalars().all())
    report = EmbeddingRunReport(
        eligible=len(passages),
        model_name=encoder.model_name,
        model_revision=encoder.model_revision,
        dimension=encoder.dimension,
    )

    existing = {
        row.owner_id: row
        for row in session.execute(
            select(Embedding).where(
                Embedding.owner_type == OWNER_TYPE_CONTEXTUAL_PASSAGE,
                Embedding.owner_id.in_([p.id for p in passages]),
            )
        )
        .scalars()
        .all()
    } if passages else {}

    pending = []
    for passage in passages:
        current = existing.get(passage.id)
        if current is not None and not force and not is_stale(
            current, source_hash=passage.source_hash, encoder=encoder
        ):
            report.skipped_current += 1
            continue
        pending.append((passage, current))

    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        try:
            vectors = encoder.encode([passage.text_content for passage, _ in batch])
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            for passage, _ in batch:
                report.failed.append(
                    {"passage_id": str(passage.id), "reason": f"encode failed: {exc}"}
                )
            continue

        for (passage, current), vector in zip(batch, vectors):
            if len(vector) != encoder.dimension:
                report.failed.append(
                    {
                        "passage_id": str(passage.id),
                        "reason": f"expected {encoder.dimension} dims, got {len(vector)}",
                    }
                )
                continue

            if current is None:
                session.add(
                    Embedding(
                        owner_type=OWNER_TYPE_CONTEXTUAL_PASSAGE,
                        owner_id=passage.id,
                        model_name=encoder.model_name,
                        model_revision=encoder.model_revision,
                        vector=vector,
                        source_hash=passage.source_hash,
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
                current.source_hash = passage.source_hash
                current.dimension = encoder.dimension
                current.normalized = encoder.normalized
                current.prep_version = PREP_VERSION
                report.regenerated_stale += 1

    session.flush()
    return report
