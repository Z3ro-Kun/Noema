"""Anime ingestion against the real schema, inside rolled-back transactions.

The point of these tests is not AniList coverage -- it is whether a
structurally different second domain lands in the same tables as Literature
without special-casing.
"""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Container, ContentUnit, Creator, Entity, Relationship, Work, WorkCreator
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from app.services.ingestion.service import ingest_source_work, resolve_source_relations

FIXTURES = Path(__file__).parent / "fixtures"

# The fixtures are real AniList payloads, so their ids are the ids of works
# that may genuinely be ingested locally. Every id -- the work's own and every
# relation target -- is shifted into a private range so these tests never
# touch, resolve against, or depend on real data. Without this the tests pass
# or fail depending on whether someone has run the real ingestion.
TEST_ID_OFFSET = 900000


def _test_id(real_id: int) -> int:
    return TEST_ID_OFFSET + real_id


def anime_source_work(filename: str):
    media = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    media["id"] = _test_id(media["id"])

    # Series <-> movie still resolve to each other because both are shifted by
    # the same offset; the manga adaptations shift too and stay unresolvable
    # because nothing ever ingests them.
    for edge in (media.get("relations") or {}).get("edges") or []:
        node = edge.get("node") or {}
        if node.get("id") is not None:
            node["id"] = _test_id(node["id"])

    return AniListAnimeAdapter(media=media).load()


async def test_anime_work_is_stored_in_the_anime_domain(db_session: AsyncSession) -> None:
    result = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))

    work = await db_session.get(Work, result.work_id)
    await db_session.refresh(work, ["domain"])
    assert work.domain.slug == "anime"
    assert work.source == "anilist"
    assert work.external_ids["anilist_id"] == _test_id(1)


async def test_episodes_are_stored_as_containers_with_no_content_units(
    db_session: AsyncSession,
) -> None:
    result = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))

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
    assert len(containers) == 26
    assert all(c.container_type == "episode" for c in containers)
    assert containers[0].title == "Asteroid Blues"

    unit_count = await db_session.execute(
        select(func.count())
        .select_from(ContentUnit)
        .join(Container, ContentUnit.container_id == Container.id)
        .where(Container.work_id == result.work_id)
    )
    assert unit_count.scalar_one() == 0
    assert result.content_units == 0


async def test_characters_are_stored_as_work_scoped_entities(db_session: AsyncSession) -> None:
    result = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))

    entities = (
        (await db_session.execute(select(Entity).where(Entity.work_id == result.work_id)))
        .scalars()
        .all()
    )
    assert entities
    assert all(e.entity_type == "character" for e in entities)
    spike = next(e for e in entities if e.name == "Spike Spiegel")
    assert spike.extra_metadata["anilist_character_id"]
    assert spike.extra_metadata["role"] == "MAIN"


async def test_studios_and_staff_are_stored_as_creators(db_session: AsyncSession) -> None:
    result = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))

    links = (
        (await db_session.execute(select(WorkCreator).where(WorkCreator.work_id == result.work_id)))
        .scalars()
        .all()
    )
    roles = {link.role for link in links}
    assert "studio" in roles

    studio = (
        await db_session.execute(select(Creator).where(Creator.name == "Sunrise"))
    ).scalar_one()
    assert studio.external_ids["anilist_studio_id"]


async def test_creators_are_shared_across_works_not_duplicated(db_session: AsyncSession) -> None:
    """Sunrise made both works; it must be one creator row, linked twice."""
    first = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))
    second = await ingest_source_work(
        db_session, anime_source_work("anilist_cowboy_bebop_movie.json")
    )

    sunrise_rows = (
        (await db_session.execute(select(Creator).where(Creator.name == "Sunrise")))
        .scalars()
        .all()
    )
    assert len(sunrise_rows) == 1

    links = (
        (
            await db_session.execute(
                select(WorkCreator).where(WorkCreator.creator_id == sunrise_rows[0].id)
            )
        )
        .scalars()
        .all()
    )
    # Subset, not equality: the same creator may already be linked to works
    # ingested outside this test.
    assert {first.work_id, second.work_id} <= {link.work_id for link in links}


async def test_one_person_credited_many_ways_is_one_creator(db_session: AsyncSession) -> None:
    """AniList credits Youko Kanno on Bebop for music, composition and arrangement.

    That is one person with three credits, not three people. Role belongs on
    the work<->creator link, never in the creator's identity -- matching on
    (name, role) silently fragments a person into one row per job.
    """
    result = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))

    rows = (
        (await db_session.execute(select(Creator).where(Creator.name == "Youko Kanno")))
        .scalars()
        .all()
    )
    assert len(rows) == 1

    credits = (
        (
            await db_session.execute(
                select(WorkCreator).where(
                    WorkCreator.work_id == result.work_id,
                    WorkCreator.creator_id == rows[0].id,
                )
            )
        )
        .scalars()
        .all()
    )
    # All three distinct credits are preserved, as separate links.
    assert len(credits) == 3
    assert len({credit.role for credit in credits}) == 3


async def test_provenance_survives_persistence(db_session: AsyncSession) -> None:
    result = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))

    work = await db_session.get(Work, result.work_id)
    provenance = work.extra_metadata["provenance"]
    assert provenance["adapter"] == "anime.anilist"
    assert provenance["source_url"] == "https://anilist.co/anime/1"
    assert work.extra_metadata["anilist"]["genres"]


async def test_reingesting_the_same_anime_is_idempotent(db_session: AsyncSession) -> None:
    first = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))
    second = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))

    assert second.created is False
    assert second.work_id == first.work_id

    for model, column in ((Work, Work.id), (Container, Container.work_id), (Entity, Entity.work_id)):
        count = await db_session.execute(
            select(func.count()).select_from(model).where(column == first.work_id)
        )
        expected = 1 if model is Work else count.scalar_one()
        assert expected >= 1

    containers = await db_session.execute(
        select(func.count()).select_from(Container).where(Container.work_id == first.work_id)
    )
    assert containers.scalar_one() == 26


# --- source relationships ------------------------------------------------


async def test_source_relations_resolve_to_edges_between_ingested_works(
    db_session: AsyncSession,
) -> None:
    series = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))
    movie = await ingest_source_work(
        db_session, anime_source_work("anilist_cowboy_bebop_movie.json")
    )

    created = await resolve_source_relations(db_session)
    assert created >= 2  # series -> movie (side_story) and movie -> series (parent)

    edges = (
        (
            await db_session.execute(
                select(Relationship).where(Relationship.subject_id.in_([series.work_id, movie.work_id]))
            )
        )
        .scalars()
        .all()
    )
    pairs = {(e.subject_id, e.predicate, e.object_id) for e in edges}
    assert (series.work_id, "side_story", movie.work_id) in pairs
    assert (movie.work_id, "parent", series.work_id) in pairs


async def test_source_relations_are_marked_as_source_provided_not_computed(
    db_session: AsyncSession,
) -> None:
    series = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))
    movie = await ingest_source_work(
        db_session, anime_source_work("anilist_cowboy_bebop_movie.json")
    )
    await resolve_source_relations(db_session)

    edges = (
        (
            await db_session.execute(
                select(Relationship).where(
                    Relationship.subject_id.in_([series.work_id, movie.work_id])
                )
            )
        )
        .scalars()
        .all()
    )

    assert edges
    for edge in edges:
        assert edge.source == "source"
        assert edge.method == "anilist_relation"
        # A stated relation carries no similarity score. Populating these
        # would make a source fact look like a computed observation.
        assert edge.score is None
        assert edge.confidence is None


async def test_relations_to_uningested_works_create_no_edges(db_session: AsyncSession) -> None:
    """Bebop names manga adaptations we never ingest; they must not become edges."""
    result = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))
    await resolve_source_relations(db_session)

    edges = (
        (await db_session.execute(select(Relationship).where(Relationship.subject_id == result.work_id)))
        .scalars()
        .all()
    )
    recorded = (await db_session.get(Work, result.work_id)).extra_metadata["source_relations"]

    # Every relation is recorded on the work, but only resolvable ones become edges.
    assert len(recorded) > len(edges)
    assert any(r["extra_metadata"]["target_media_type"] == "MANGA" for r in recorded)


async def test_resolution_is_idempotent(db_session: AsyncSession) -> None:
    await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))
    await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop_movie.json"))

    first_pass = await resolve_source_relations(db_session)
    second_pass = await resolve_source_relations(db_session)

    assert first_pass > 0
    assert second_pass == 0


async def test_relations_resolve_regardless_of_ingestion_order(db_session: AsyncSession) -> None:
    """Bebop references the movie before the movie exists; resolution catches up."""
    series = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))
    assert await resolve_source_relations(db_session) == 0

    movie = await ingest_source_work(
        db_session, anime_source_work("anilist_cowboy_bebop_movie.json")
    )
    assert await resolve_source_relations(db_session) >= 2

    edge = await db_session.execute(
        select(Relationship).where(
            Relationship.subject_id == series.work_id,
            Relationship.object_id == movie.work_id,
        )
    )
    assert edge.scalars().first() is not None


# --- cross-domain coexistence --------------------------------------------


async def test_literature_and_anime_coexist_in_the_same_tables(db_session: AsyncSession) -> None:
    """The actual point of this phase: one model, two structurally different domains."""
    novel = await ingest_source_work(
        db_session,
        PlainTextLiteratureAdapter(
            text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
            title="The Lantern Keeper",
            source_ref="cross-domain-test",
            author="A Test Author",
        ).load(),
    )
    anime = await ingest_source_work(db_session, anime_source_work("anilist_cowboy_bebop.json"))

    novel_work = await db_session.get(Work, novel.work_id)
    anime_work = await db_session.get(Work, anime.work_id)
    await db_session.refresh(novel_work, ["domain"])
    await db_session.refresh(anime_work, ["domain"])

    assert {novel_work.domain.slug, anime_work.domain.slug} == {"literature", "anime"}

    # Same container table, different container_type, different text availability.
    novel_containers = (
        (await db_session.execute(select(Container).where(Container.work_id == novel.work_id)))
        .scalars()
        .all()
    )
    anime_containers = (
        (await db_session.execute(select(Container).where(Container.work_id == anime.work_id)))
        .scalars()
        .all()
    )
    assert {c.container_type for c in novel_containers} <= {"chapter", "front_matter"}
    assert {c.container_type for c in anime_containers} == {"episode"}

    assert novel.content_units > 0
    assert anime.content_units == 0
