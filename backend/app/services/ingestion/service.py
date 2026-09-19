"""Persists a normalized `SourceWork` into Domain -> Work -> Container -> ContentUnit.

Domain-agnostic on purpose: it only ever sees the normalized representation,
so the anime and manhwa adapters will reuse it unchanged.

This service flushes but does not commit -- the caller owns the transaction,
which lets the CLI commit and tests roll back.
"""

import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Container,
    ContentUnit,
    Creator,
    Domain,
    Entity,
    Relationship,
    Work,
    WorkCreator,
)
from app.services.ingestion.normalized import SourceCreator, SourceWork

# Marks an edge as something a source asserted, never something we computed.
SOURCE_PROVIDED = "source"


class UnknownDomainError(ValueError):
    """Raised when a source names a domain that isn't in the domains table."""


@dataclass
class IngestionResult:
    work_id: uuid.UUID
    created: bool
    containers: int
    content_units: int
    entities: int = 0
    relations_recorded: int = 0


async def find_existing_work(session: AsyncSession, source: str, source_ref: str) -> Work | None:
    """Look up a work by its (source, source_ref) idempotency key."""
    result = await session.execute(
        select(Work).where(
            Work.source == source,
            Work.external_ids["source_ref"].astext == source_ref,
        )
    )
    return result.scalar_one_or_none()


async def _get_or_create_creator(session: AsyncSession, source_creator: SourceCreator) -> Creator:
    """Find a creator by external id where the source gives one, else by name.

    Role is deliberately *not* part of the identity. A person is one person
    however many jobs they did: AniList credits Shinichirou Watanabe on
    Cowboy Bebop as director, storyboarder, and scriptwriter, and matching on
    (name, role) would make him three creators. The per-work credit belongs
    on `WorkCreator.role`, which is exactly what that column is for.

    Without an external id this falls back to matching on name alone, which
    will merge two different people who share a name. That is a known limit
    of having no entity resolution yet; the external id path avoids it
    wherever the source supplies one.
    """
    external_ids = source_creator.external_ids or {}
    id_key = next(
        (key for key, value in external_ids.items() if key != "source_ref" and value is not None),
        None,
    )

    creator = None
    if id_key is not None:
        creator = (
            await session.execute(
                select(Creator)
                .where(Creator.external_ids[id_key].astext == str(external_ids[id_key]))
                .limit(1)
            )
        ).scalar_one_or_none()

    if creator is None:
        creator = (
            await session.execute(
                select(Creator).where(Creator.name == source_creator.name).limit(1)
            )
        ).scalar_one_or_none()

    if creator is None:
        creator = Creator(
            # The first role we see, kept as description only. The
            # authoritative per-work credit is WorkCreator.role.
            name=source_creator.name,
            role=source_creator.role,
            external_ids=source_creator.external_ids,
        )
        session.add(creator)
        await session.flush()
    return creator


async def ingest_source_work(session: AsyncSession, source_work: SourceWork) -> IngestionResult:
    """Persist a normalized work. Re-ingesting the same source is a no-op."""
    domain = (
        await session.execute(select(Domain).where(Domain.slug == source_work.domain_slug))
    ).scalar_one_or_none()
    if domain is None:
        raise UnknownDomainError(
            f"domain '{source_work.domain_slug}' is not registered; "
            "run `alembic upgrade head` to seed the domains table"
        )

    existing = await find_existing_work(session, source_work.source, source_work.source_ref)
    if existing is not None:
        return IngestionResult(
            work_id=existing.id,
            created=False,
            containers=0,
            content_units=0,
        )

    # Relations are stored on the work so they can be resolved later: a work
    # can reference something that hasn't been ingested yet (or ever).
    extra_metadata = dict(source_work.extra_metadata or {})
    if source_work.relations:
        extra_metadata["source_relations"] = [asdict(r) for r in source_work.relations]

    work = Work(
        domain_id=domain.id,
        title=source_work.title,
        original_title=source_work.original_title,
        description=source_work.description,
        source=source_work.source,
        external_ids=source_work.external_ids,
        extra_metadata=extra_metadata or None,
    )
    session.add(work)
    await session.flush()

    linked_creators: set[tuple[uuid.UUID, str]] = set()
    for source_creator in source_work.creators:
        creator = await _get_or_create_creator(session, source_creator)
        # A source can credit the same person twice under one role; the
        # (work, creator, role) primary key would reject the duplicate.
        if (creator.id, source_creator.role) in linked_creators:
            continue
        linked_creators.add((creator.id, source_creator.role))
        session.add(WorkCreator(work_id=work.id, creator_id=creator.id, role=source_creator.role))

    for source_entity in source_work.entities:
        session.add(
            Entity(
                work_id=work.id,
                name=source_entity.name,
                entity_type=source_entity.entity_type,
                description=source_entity.description,
                extra_metadata={
                    **(source_entity.extra_metadata or {}),
                    **(source_entity.external_ids or {}),
                },
            )
        )

    unit_count = 0
    for source_container in source_work.containers:
        container = Container(
            work_id=work.id,
            container_type=source_container.container_type,
            sequence_number=source_container.sequence_number,
            title=source_container.title,
            extra_metadata=source_container.extra_metadata,
        )
        session.add(container)
        await session.flush()

        session.add_all(
            [
                ContentUnit(
                    container_id=container.id,
                    unit_type=unit.unit_type,
                    sequence_number=unit.sequence_number,
                    text_content=unit.text_content,
                    extra_metadata=unit.extra_metadata,
                )
                for unit in source_container.content_units
            ]
        )
        unit_count += len(source_container.content_units)

    await session.flush()

    return IngestionResult(
        work_id=work.id,
        created=True,
        containers=len(source_work.containers),
        content_units=unit_count,
        entities=len(source_work.entities),
        relations_recorded=len(source_work.relations),
    )


async def resolve_source_relations(session: AsyncSession) -> int:
    """Turn recorded source relations into edges, where both ends exist locally.

    Separate from ingestion because resolution is order-dependent: when work A
    names B as its sequel, B may not be ingested until later. Re-running this
    is safe and is how those edges eventually appear.

    Only ever writes `source="source"` edges with no score or confidence --
    these are facts a source asserted, not similarities we measured. Nothing
    here infers a relationship that wasn't explicitly stated.
    """
    works = (
        (await session.execute(select(Work).where(Work.extra_metadata.has_key("source_relations"))))
        .scalars()
        .all()
    )

    created = 0
    for work in works:
        for relation in work.extra_metadata.get("source_relations") or []:
            target = await find_existing_work(
                session, relation["target_source"], relation["target_source_ref"]
            )
            if target is None:
                continue

            exists = (
                await session.execute(
                    select(Relationship.id).where(
                        Relationship.subject_type == "work",
                        Relationship.subject_id == work.id,
                        Relationship.predicate == relation["predicate"],
                        Relationship.object_type == "work",
                        Relationship.object_id == target.id,
                        Relationship.source == SOURCE_PROVIDED,
                    )
                )
            ).first()
            if exists is not None:
                continue

            session.add(
                Relationship(
                    subject_type="work",
                    subject_id=work.id,
                    predicate=relation["predicate"],
                    object_type="work",
                    object_id=target.id,
                    source=SOURCE_PROVIDED,
                    method=f"{work.source}_relation",
                    # Deliberately null: a stated relation has no score, and a
                    # score here would make it look like a computed similarity.
                    score=None,
                    confidence=None,
                )
            )
            created += 1

    await session.flush()
    return created
