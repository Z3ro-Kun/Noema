"""Manga/manhwa ingestion against the real schema, inside rolled-back transactions.

The point of these tests is not AniList coverage -- it is whether a *third*
domain, with its own unit of structure (volumes, not episodes or chapters),
lands in the same tables as Literature and Anime without special-casing.
"""

import json
from dataclasses import replace
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Container, ContentUnit, Creator, Entity, Relationship, Work, WorkCreator
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from app.services.ingestion.manga import AniListMangaAdapter
from app.services.ingestion.service import (
    find_existing_work,
    ingest_source_work,
    resolve_source_relations,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Same convention as the anime ingestion tests: the fixtures are real AniList
# payloads, so every id -- the work's own and every relation target -- is
# shifted into a private range. Without this, these tests would pass or fail
# depending on whether the real corpus happens to be ingested locally.
TEST_ID_OFFSET = 900000


def _test_id(real_id: int) -> int:
    return TEST_ID_OFFSET + real_id


def _shift(media: dict) -> dict:
    media["id"] = _test_id(media["id"])
    for edge in (media.get("relations") or {}).get("edges") or []:
        node = edge.get("node") or {}
        if node.get("id") is not None:
            node["id"] = _test_id(node["id"])
    return media


def manga_source_work(filename: str):
    media = _shift(json.loads((FIXTURES / filename).read_text(encoding="utf-8")))
    return AniListMangaAdapter(media=media).load()


def anime_source_work(filename: str):
    media = _shift(json.loads((FIXTURES / filename).read_text(encoding="utf-8")))
    return AniListAnimeAdapter(media=media).load()


# --- work, domain, structure ---------------------------------------------


async def test_manga_work_is_stored_in_the_comics_domain(db_session: AsyncSession) -> None:
    result = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_vinland_saga.json")
    )

    work = await db_session.get(Work, result.work_id)
    await db_session.refresh(work, ["domain"])
    assert work.domain.slug == "manhwa"
    assert work.source == "anilist"
    assert work.external_ids["anilist_id"] == _test_id(30642)


async def test_manhwa_lands_in_the_same_domain_as_manga(db_session: AsyncSession) -> None:
    """One domain holds both traditions; the tradition itself stays on the work."""
    manga = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_vinland_saga.json")
    )
    manhwa = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_solo_leveling.json")
    )

    domains = set()
    traditions = set()
    for work_id in (manga.work_id, manhwa.work_id):
        work = await db_session.get(Work, work_id)
        await db_session.refresh(work, ["domain"])
        domains.add(work.domain.slug)
        traditions.add(work.extra_metadata["anilist"]["comic_tradition"])

    assert domains == {"manhwa"}
    assert traditions == {"manga", "manhwa"}


async def test_volumes_are_stored_as_containers_with_no_content_units(
    db_session: AsyncSession,
) -> None:
    result = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_vinland_saga.json")
    )

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
    assert len(containers) == 29
    assert all(c.container_type == "volume" for c in containers)

    unit_count = await db_session.execute(
        select(func.count())
        .select_from(ContentUnit)
        .join(Container, ContentUnit.container_id == Container.id)
        .where(Container.work_id == result.work_id)
    )
    assert unit_count.scalar_one() == 0
    assert result.content_units == 0


async def test_container_order_is_preserved_by_sequence_number(db_session: AsyncSession) -> None:
    """Volume 1 through 29, contiguous, in order -- not list position."""
    result = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_vinland_saga.json")
    )

    numbers = (
        (
            await db_session.execute(
                select(Container.sequence_number)
                .where(Container.work_id == result.work_id)
                .order_by(Container.sequence_number)
            )
        )
        .scalars()
        .all()
    )
    assert numbers == list(range(1, 30))

    first = (
        await db_session.execute(
            select(Container).where(
                Container.work_id == result.work_id, Container.sequence_number == 1
            )
        )
    ).scalar_one()
    assert first.extra_metadata["volume_number"] == 1
    assert first.extra_metadata["has_source_text"] is False


# --- creators and entities -----------------------------------------------


async def test_staff_are_stored_as_creators_with_roles_on_the_link(
    db_session: AsyncSession,
) -> None:
    result = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_vinland_saga.json")
    )

    author = (
        await db_session.execute(select(Creator).where(Creator.name == "Makoto Yukimura"))
    ).scalar_one()
    assert author.external_ids["anilist_staff_id"]

    link = (
        await db_session.execute(
            select(WorkCreator).where(
                WorkCreator.work_id == result.work_id, WorkCreator.creator_id == author.id
            )
        )
    ).scalar_one()
    assert link.role == "Story & Art"


async def test_characters_are_stored_as_work_scoped_entities(db_session: AsyncSession) -> None:
    result = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_solo_leveling.json")
    )

    entities = (
        (await db_session.execute(select(Entity).where(Entity.work_id == result.work_id)))
        .scalars()
        .all()
    )
    assert entities
    assert all(e.entity_type == "character" for e in entities)
    protagonist = next(e for e in entities if e.name == "Jin-U Seong")
    assert protagonist.extra_metadata["anilist_character_id"]
    assert protagonist.extra_metadata["role"] == "MAIN"


# --- provenance ----------------------------------------------------------


async def test_provenance_survives_persistence(db_session: AsyncSession) -> None:
    result = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_vinland_saga.json")
    )

    work = await db_session.get(Work, result.work_id)
    provenance = work.extra_metadata["provenance"]
    assert provenance["adapter"] == "manga.anilist"
    assert provenance["source_name"] == "anilist"
    assert "No chapter text, scans, or artwork" in provenance["license_note"]
    assert work.extra_metadata["anilist"]["genres"]


# --- idempotency ---------------------------------------------------------


async def test_reingesting_the_same_manga_is_idempotent(db_session: AsyncSession) -> None:
    first = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_vinland_saga.json")
    )
    second = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_vinland_saga.json")
    )

    assert second.created is False
    assert second.work_id == first.work_id

    containers = await db_session.execute(
        select(func.count()).select_from(Container).where(Container.work_id == first.work_id)
    )
    assert containers.scalar_one() == 29

    entities = await db_session.execute(
        select(func.count()).select_from(Entity).where(Entity.work_id == first.work_id)
    )
    assert entities.scalar_one() == 4


async def test_identity_is_the_source_ref_and_holds_across_domains(
    db_session: AsyncSession,
) -> None:
    """The (source, source_ref) key is not domain-scoped, and does not need to be.

    AniList keeps one id space for both media types -- querying id 1 as MANGA
    or id 30642 as ANIME returns nothing -- so an AniList id names exactly one
    work whichever domain it lands in. The anime and manga adapters can
    therefore share `source="anilist"` without namespacing their refs.

    A future comics source with per-type id spaces would break this, which is
    why the lookup is asserted here rather than assumed.
    """
    anime = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))
    manga = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_vinland_saga.json")
    )

    assert manga.work_id != anime.work_id

    found_anime = await find_existing_work(db_session, "anilist", str(_test_id(1)))
    found_manga = await find_existing_work(db_session, "anilist", str(_test_id(30642)))

    assert found_anime is not None and found_anime.id == anime.work_id
    assert found_manga is not None and found_manga.id == manga.work_id


# --- malformed source ----------------------------------------------------


async def test_a_work_for_an_unknown_domain_is_refused(db_session: AsyncSession) -> None:
    """A typo in the domain slug must fail loudly, not create a domain."""
    from app.services.ingestion.service import UnknownDomainError

    # A real and common transposition of the slug.
    source_work = replace(
        manga_source_work("anilist_manga_vinland_saga.json"), domain_slug="manwha"
    )

    try:
        await ingest_source_work(db_session, source_work)
    except UnknownDomainError:
        pass
    else:
        raise AssertionError("ingestion accepted an unknown domain slug")

    await db_session.rollback()
    # Scoped to the fixture's shifted id, never the title: the real corpus
    # holds a Vinland Saga of its own and this test must not see it.
    assert await find_existing_work(db_session, "anilist", str(_test_id(30642))) is None


# --- source relationships ------------------------------------------------


async def test_cross_domain_relations_resolve_to_edges(db_session: AsyncSession) -> None:
    """The manga and its anime adaptation are different domains and one edge.

    Vinland Saga's AniList entry names anime 101348 as an adaptation. The
    anime fixture here is Cowboy Bebop renumbered to that id, because what is
    under test is that resolution crosses the domain boundary at all -- not
    which anime it is.
    """
    manga = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_vinland_saga.json")
    )

    adaptation = json.loads((FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8"))
    adaptation["id"] = 101348
    adaptation["relations"] = None  # only the manga's own claim is under test
    anime = await ingest_source_work(
        db_session, AniListAnimeAdapter(media=_shift(adaptation)).load()
    )

    assert await resolve_source_relations(db_session) >= 1

    edge = (
        await db_session.execute(
            select(Relationship).where(
                Relationship.subject_id == manga.work_id,
                Relationship.object_id == anime.work_id,
            )
        )
    ).scalar_one()
    assert edge.predicate == "adaptation"
    assert edge.source == "source"
    assert edge.method == "anilist_relation"
    # A stated relation carries no similarity score.
    assert edge.score is None


async def test_relations_to_uningested_works_create_no_edges(db_session: AsyncSession) -> None:
    """Vinland Saga names spin-offs we never ingest; they must not become edges."""
    result = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_vinland_saga.json")
    )
    await resolve_source_relations(db_session)

    edges = (
        (
            await db_session.execute(
                select(Relationship).where(Relationship.subject_id == result.work_id)
            )
        )
        .scalars()
        .all()
    )
    recorded = (await db_session.get(Work, result.work_id)).extra_metadata["source_relations"]

    assert len(recorded) > len(edges)
    assert any(r["predicate"] == "spin_off" for r in recorded)


# --- three-domain coexistence --------------------------------------------


async def test_all_three_domains_coexist_in_the_same_tables(db_session: AsyncSession) -> None:
    """The actual point of this phase: one model, three units of structure."""
    novel = await ingest_source_work(
        db_session,
        PlainTextLiteratureAdapter(
            text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
            title="The Lantern Keeper",
            source_ref="three-domain-test",
            author="A Test Author",
        ).load(),
    )
    anime = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))
    manga = await ingest_source_work(
        db_session, manga_source_work("anilist_manga_vinland_saga.json")
    )

    slugs = set()
    for work_id in (novel.work_id, anime.work_id, manga.work_id):
        work = await db_session.get(Work, work_id)
        await db_session.refresh(work, ["domain"])
        slugs.add(work.domain.slug)
    assert slugs == {"literature", "anime", "manhwa"}

    async def container_types(work_id):
        rows = (
            (
                await db_session.execute(
                    select(Container.container_type).where(Container.work_id == work_id)
                )
            )
            .scalars()
            .all()
        )
        return set(rows)

    assert await container_types(novel.work_id) <= {"chapter", "front_matter"}
    assert await container_types(anime.work_id) == {"episode"}
    assert await container_types(manga.work_id) == {"volume"}

    # Only literature arrives with its own words; the other two carry none
    # until a summary source supplies them.
    assert novel.content_units > 0
    assert anime.content_units == 0
    assert manga.content_units == 0
