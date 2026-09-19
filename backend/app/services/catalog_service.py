"""Read-only queries over the ingested domain model.

Kept out of the API layer so routes stay thin and the same queries can be
reused by workers and CLIs later.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Concept, Container, ContentUnit, Domain, Entity, Relationship, Work, WorkConcept


async def list_domains(session: AsyncSession) -> list[Domain]:
    result = await session.execute(select(Domain).order_by(Domain.slug))
    return list(result.scalars().all())


async def list_works(
    session: AsyncSession,
    domain_slug: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Work]:
    query = select(Work).options(selectinload(Work.domain)).order_by(Work.title)
    if domain_slug is not None:
        query = query.join(Domain).where(Domain.slug == domain_slug)
    result = await session.execute(query.limit(limit).offset(offset))
    return list(result.scalars().all())


async def get_work(session: AsyncSession, work_id: uuid.UUID) -> Work | None:
    result = await session.execute(
        select(Work).options(selectinload(Work.domain)).where(Work.id == work_id)
    )
    return result.scalar_one_or_none()


async def list_containers(session: AsyncSession, work_id: uuid.UUID) -> list[Container]:
    result = await session.execute(
        select(Container)
        .where(Container.work_id == work_id)
        .order_by(Container.sequence_number)
    )
    return list(result.scalars().all())


async def count_content_units(session: AsyncSession, container_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ContentUnit)
        .where(ContentUnit.container_id == container_id)
    )
    return int(result.scalar_one())


async def get_container(session: AsyncSession, container_id: uuid.UUID) -> Container | None:
    result = await session.execute(select(Container).where(Container.id == container_id))
    return result.scalar_one_or_none()


async def list_entities(
    session: AsyncSession, work_id: uuid.UUID, limit: int = 100
) -> list[Entity]:
    result = await session.execute(
        select(Entity).where(Entity.work_id == work_id).order_by(Entity.name).limit(limit)
    )
    return list(result.scalars().all())


async def list_work_relationships(
    session: AsyncSession, work_id: uuid.UUID
) -> list[tuple[Relationship, str | None]]:
    """Relationships where this work is the subject, with the target's title.

    Returns (relationship, target_title) so callers can label an edge without
    a second round trip. The title is None when the target isn't a work.
    """
    target = Work.__table__.alias("target_work")
    result = await session.execute(
        select(Relationship, target.c.title)
        .outerjoin(target, Relationship.object_id == target.c.id)
        .where(Relationship.subject_type == "work", Relationship.subject_id == work_id)
        .order_by(Relationship.predicate)
    )
    return [(row[0], row[1]) for row in result.all()]


async def list_content_units(
    session: AsyncSession,
    container_id: uuid.UUID,
    limit: int = 100,
    offset: int = 0,
) -> list[ContentUnit]:
    result = await session.execute(
        select(ContentUnit)
        .options(selectinload(ContentUnit.text_source))
        .where(ContentUnit.container_id == container_id)
        # Primary text first: where a container has both, the work's own words
        # outrank a third party's description of them. ("primary" sorts before
        # "summary" alphabetically, which is the order we want.)
        .order_by(ContentUnit.text_tier, ContentUnit.sequence_number)
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def list_work_concepts(session: AsyncSession, work_id: uuid.UUID) -> list[tuple]:
    """A work's concepts, strongest stated relevance first.

    Ranked associations come before unranked ones rather than mixed with
    them: a null confidence means the source stated no relevance, which is
    not the same as stating a low one.
    """
    rows = await session.execute(
        select(WorkConcept, Concept)
        .join(Concept, WorkConcept.concept_id == Concept.id)
        .where(WorkConcept.work_id == work_id)
        .order_by(WorkConcept.confidence.desc().nullslast(), Concept.name)
    )
    return [(row[0], row[1]) for row in rows.all()]
