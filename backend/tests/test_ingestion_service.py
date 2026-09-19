"""Ingestion service tests.

These run inside a transaction that is always rolled back, so they exercise
the real Postgres schema without leaving rows behind.
"""

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Container, ContentUnit, Work, WorkCreator
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from app.services.ingestion.normalized import SourceWork
from app.services.ingestion.service import (
    UnknownDomainError,
    find_existing_work,
    ingest_source_work,
)

FIXTURES = Path(__file__).parent / "fixtures"


def sample_source_work(source_ref: str = "ingest-test-001") -> SourceWork:
    return PlainTextLiteratureAdapter(
        text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
        title="The Lantern Keeper",
        source_ref=source_ref,
        author="A Test Author",
        source_url="https://example.invalid/lantern",
    ).load()


async def test_ingestion_creates_work_with_domain_and_provenance(db_session: AsyncSession) -> None:
    source_work = sample_source_work()

    result = await ingest_source_work(db_session, source_work)

    assert result.created is True
    work = await db_session.get(Work, result.work_id)
    assert work.title == "The Lantern Keeper"
    assert work.source == "gutenberg"
    assert work.external_ids["source_ref"] == "ingest-test-001"
    assert work.extra_metadata["provenance"]["source_url"] == "https://example.invalid/lantern"
    assert len(work.extra_metadata["provenance"]["content_sha256"]) == 64


async def test_ingested_work_is_attached_to_the_literature_domain(
    db_session: AsyncSession,
) -> None:
    result = await ingest_source_work(db_session, sample_source_work())

    work = await db_session.get(Work, result.work_id)
    await db_session.refresh(work, ["domain"])
    assert work.domain.slug == "literature"


async def test_containers_and_content_units_are_linked_in_order(
    db_session: AsyncSession,
) -> None:
    result = await ingest_source_work(db_session, sample_source_work())

    containers = (
        (
            await db_session.execute(
                select(Container)
                .where(Container.work_id == result.work_id)
                .order_by(Container.sequence_number)
            )
        )
        .scalars()
        .all()
    )
    chapters = [c for c in containers if c.container_type == "chapter"]
    assert [c.title for c in chapters] == ["The Harbour", "The Storm"]

    units = (
        (
            await db_session.execute(
                select(ContentUnit)
                .where(ContentUnit.container_id == chapters[0].id)
                .order_by(ContentUnit.sequence_number)
            )
        )
        .scalars()
        .all()
    )
    assert [u.sequence_number for u in units] == [1, 2]
    assert units[0].text_content.startswith("The lantern keeper woke")
    assert all(u.unit_type == "passage" for u in units)


async def test_content_unit_total_matches_reported_result(db_session: AsyncSession) -> None:
    result = await ingest_source_work(db_session, sample_source_work())

    stored = await db_session.execute(
        select(func.count())
        .select_from(ContentUnit)
        .join(Container, ContentUnit.container_id == Container.id)
        .where(Container.work_id == result.work_id)
    )
    assert stored.scalar_one() == result.content_units


async def test_creator_is_linked_to_the_work(db_session: AsyncSession) -> None:
    result = await ingest_source_work(db_session, sample_source_work())

    links = (
        (await db_session.execute(select(WorkCreator).where(WorkCreator.work_id == result.work_id)))
        .scalars()
        .all()
    )
    assert len(links) == 1
    assert links[0].role == "author"


async def test_reingesting_the_same_source_is_idempotent(db_session: AsyncSession) -> None:
    first = await ingest_source_work(db_session, sample_source_work())
    second = await ingest_source_work(db_session, sample_source_work())

    assert second.created is False
    assert second.work_id == first.work_id

    work_count = await db_session.execute(
        select(func.count())
        .select_from(Work)
        .where(Work.external_ids["source_ref"].astext == "ingest-test-001")
    )
    assert work_count.scalar_one() == 1

    container_count = await db_session.execute(
        select(func.count()).select_from(Container).where(Container.work_id == first.work_id)
    )
    assert container_count.scalar_one() == first.containers


async def test_different_source_refs_create_separate_works(db_session: AsyncSession) -> None:
    first = await ingest_source_work(db_session, sample_source_work("ingest-test-a"))
    second = await ingest_source_work(db_session, sample_source_work("ingest-test-b"))

    assert second.created is True
    assert second.work_id != first.work_id


async def test_find_existing_work_returns_none_for_unknown_source(
    db_session: AsyncSession,
) -> None:
    assert await find_existing_work(db_session, "gutenberg", "definitely-not-ingested") is None


async def test_unknown_domain_is_rejected(db_session: AsyncSession) -> None:
    source_work = sample_source_work()
    bad_domain = SourceWork(
        domain_slug="webtoon-that-does-not-exist",
        source=source_work.source,
        source_ref="bad-domain",
        title=source_work.title,
        containers=source_work.containers,
    )

    with pytest.raises(UnknownDomainError):
        await ingest_source_work(db_session, bad_domain)
