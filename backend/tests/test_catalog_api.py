"""Read-only catalog API tests.

Unlike the ingestion service tests these need *committed* data, since the
API reads through the app's own sessions. The `ingested_work` fixture commits
a work and deletes it again afterwards, using a source_ref that no real
corpus uses.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import Work
from app.services.concepts.service import (
    anilist_labels,
    apply_source_labels,
    ensure_vocabulary,
)
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from app.services.ingestion.service import ingest_source_work, resolve_source_relations

FIXTURES = Path(__file__).parent / "fixtures"
API_TEST_SOURCE_REF = "api-test-lantern"
# Distinct from the real AniList ids so these tests never touch (or delete)
# genuinely ingested works.
API_TEST_ANIME_IDS = (999001, 999002)


@pytest.fixture
async def ingested_work(database_available: bool) -> AsyncIterator[str]:
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    source_work = PlainTextLiteratureAdapter(
        text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
        title="The Lantern Keeper (API test)",
        source_ref=API_TEST_SOURCE_REF,
        author="A Test Author",
        source_url="https://example.invalid/lantern",
    ).load()

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            result = await ingest_source_work(session, source_work)
            await session.commit()

        yield str(result.work_id)

        async with factory() as session:
            await session.execute(
                text(
                    "DELETE FROM content_units WHERE container_id IN "
                    "(SELECT id FROM containers WHERE work_id = :wid)"
                ),
                {"wid": result.work_id},
            )
            await session.execute(
                text("DELETE FROM containers WHERE work_id = :wid"), {"wid": result.work_id}
            )
            await session.execute(
                text("DELETE FROM work_creators WHERE work_id = :wid"), {"wid": result.work_id}
            )
            await session.execute(
                text("DELETE FROM works WHERE id = :wid"), {"wid": result.work_id}
            )
            # The creator is created by ingestion too, and is left dangling
            # once its only work is gone.
            await session.execute(
                text(
                    "DELETE FROM creators WHERE name = :name "
                    "AND NOT EXISTS (SELECT 1 FROM work_creators wc WHERE wc.creator_id = creators.id)"
                ),
                {"name": "A Test Author"},
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _delete_works(factory, work_ids: list) -> None:
    async with factory() as session:
        for work_id in work_ids:
            await session.execute(
                text(
                    "DELETE FROM relationships WHERE subject_id = :wid OR object_id = :wid"
                ),
                {"wid": work_id},
            )
            await session.execute(
                text(
                    "DELETE FROM content_units WHERE container_id IN "
                    "(SELECT id FROM containers WHERE work_id = :wid)"
                ),
                {"wid": work_id},
            )
            await session.execute(
                text("DELETE FROM containers WHERE work_id = :wid"), {"wid": work_id}
            )
            await session.execute(
                text("DELETE FROM entities WHERE work_id = :wid"), {"wid": work_id}
            )
            await session.execute(
                text("DELETE FROM work_creators WHERE work_id = :wid"), {"wid": work_id}
            )
            await session.execute(text("DELETE FROM works WHERE id = :wid"), {"wid": work_id})
        await session.execute(
            text(
                "DELETE FROM creators WHERE NOT EXISTS "
                "(SELECT 1 FROM work_creators wc WHERE wc.creator_id = creators.id)"
            )
        )
        await session.commit()


@pytest.fixture
async def ingested_anime(database_available: bool) -> AsyncIterator[tuple[str, str]]:
    """Two related anime works, so a source relationship edge exists to expose."""
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    series = json.loads((FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8"))
    movie = json.loads((FIXTURES / "anilist_cowboy_bebop_movie.json").read_text(encoding="utf-8"))
    # Rewrite ids so this never collides with really-ingested works.
    series["id"], movie["id"] = API_TEST_ANIME_IDS
    series["relations"] = {
        "edges": [
            {
                "relationType": "SIDE_STORY",
                "node": {"id": API_TEST_ANIME_IDS[1], "type": "ANIME", "title": {"romaji": "m"}},
            }
        ]
    }
    movie["relations"] = {"edges": []}

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            series_result = await ingest_source_work(session, AniListAnimeAdapter(media=series).load())
            movie_result = await ingest_source_work(session, AniListAnimeAdapter(media=movie).load())
            await resolve_source_relations(session)
            await session.commit()

        yield str(series_result.work_id), str(movie_result.work_id)

        await _delete_works(factory, [series_result.work_id, movie_result.work_id])
    finally:
        await engine.dispose()


def test_domains_endpoint_lists_all_three_domains(client: TestClient) -> None:
    response = client.get("/api/v1/domains")

    assert response.status_code == 200
    slugs = {domain["slug"] for domain in response.json()}
    assert {"literature", "anime", "manhwa"} <= slugs


def works_by_id(client: TestClient, **params) -> dict:
    """The whole /works listing keyed by work id.

    The endpoint returns a `WorkListResponse` page of `WorkPresentation`
    objects -- canonical `work` plus the caller's `user_state` -- rather than
    bare works, so tests reach through `["items"]` and `["work"]`
    deliberately.

    Every page is walked, because the listing is ordered by title and these
    tests ask whether a particular work is *in the corpus*, not whether it
    happens to sort onto the first page. Reading one page would make them a
    test of how many works the corpus holds and of how the fixture titles
    alphabetise, both of which are free to change.
    """
    collected: dict = {}
    page = 1
    while True:
        response = client.get(
            "/api/v1/works", params={**params, "page": page, "page_size": 100}
        )
        assert response.status_code == 200
        body = response.json()
        collected.update({entry["work"]["id"]: entry for entry in body["items"]})
        if not body["items"] or len(collected) >= body["total"]:
            return collected
        page += 1


def test_works_endpoint_returns_the_ingested_work(client: TestClient, ingested_work: str) -> None:
    works = works_by_id(client)

    assert ingested_work in works
    assert works[ingested_work]["work"]["domain"]["slug"] == "literature"


def test_works_can_be_filtered_by_domain(client: TestClient, ingested_work: str) -> None:
    literature = works_by_id(client, domain="literature")
    anime = works_by_id(client, domain="anime")

    assert ingested_work in literature
    assert ingested_work not in anime


def test_internal_work_detail_exposes_provenance(
    client: TestClient, ingested_work: str
) -> None:
    """Ingestion provenance moved to the explicitly-internal endpoint."""
    response = client.get(f"/api/v1/works/{ingested_work}/internal")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "gutenberg"
    assert body["external_ids"]["source_ref"] == API_TEST_SOURCE_REF
    provenance = body["extra_metadata"]["provenance"]
    assert provenance["source_url"] == "https://example.invalid/lantern"
    assert provenance["adapter"] == "literature.plain_text"


def test_containers_endpoint_returns_chapters_in_order(
    client: TestClient, ingested_work: str
) -> None:
    response = client.get(f"/api/v1/works/{ingested_work}/containers")

    assert response.status_code == 200
    containers = response.json()
    chapters = [c for c in containers if c["container_type"] == "chapter"]
    assert [c["title"] for c in chapters] == ["The Harbour", "The Storm"]
    assert all(c["content_unit_count"] > 0 for c in chapters)


def test_content_units_endpoint_returns_passages(client: TestClient, ingested_work: str) -> None:
    containers = client.get(f"/api/v1/works/{ingested_work}/containers").json()
    chapter = next(c for c in containers if c["container_type"] == "chapter")

    response = client.get(f"/api/v1/containers/{chapter['id']}/content-units")

    assert response.status_code == 200
    units = response.json()
    assert [u["sequence_number"] for u in units] == [1, 2]
    assert units[0]["text_content"].startswith("The lantern keeper woke")


def test_content_units_are_paginated(client: TestClient, ingested_work: str) -> None:
    containers = client.get(f"/api/v1/works/{ingested_work}/containers").json()
    chapter = next(c for c in containers if c["container_type"] == "chapter")

    page = client.get(
        f"/api/v1/containers/{chapter['id']}/content-units", params={"limit": 1, "offset": 1}
    ).json()

    assert len(page) == 1
    assert page[0]["sequence_number"] == 2


def test_unknown_work_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/works/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_unknown_container_returns_404(client: TestClient) -> None:
    response = client.get(
        "/api/v1/containers/00000000-0000-0000-0000-000000000000/content-units"
    )

    assert response.status_code == 404


# --- anime through the same endpoints ------------------------------------


def test_anime_works_are_listed_under_the_anime_domain(
    client: TestClient, ingested_anime: tuple[str, str]
) -> None:
    series_id, _ = ingested_anime

    works = works_by_id(client, domain="anime")

    assert series_id in works
    assert all(p["work"]["domain"]["slug"] == "anime" for p in works.values())


def test_both_domains_are_served_by_the_same_endpoint(
    client: TestClient, ingested_work: str, ingested_anime: tuple[str, str]
) -> None:
    by_id = works_by_id(client)

    assert by_id[ingested_work]["work"]["domain"]["slug"] == "literature"
    assert by_id[ingested_anime[0]]["work"]["domain"]["slug"] == "anime"


def test_anime_episodes_are_containers_with_zero_content_units(
    client: TestClient, ingested_anime: tuple[str, str]
) -> None:
    series_id, _ = ingested_anime

    containers = client.get(f"/api/v1/works/{series_id}/containers").json()

    assert len(containers) == 26
    assert all(c["container_type"] == "episode" for c in containers)
    assert all(c["content_unit_count"] == 0 for c in containers)


def test_content_units_endpoint_returns_empty_not_404_for_an_episode(
    client: TestClient, ingested_anime: tuple[str, str]
) -> None:
    """An episode with no text is a real container, not a missing one."""
    series_id, _ = ingested_anime
    container = client.get(f"/api/v1/works/{series_id}/containers").json()[0]

    response = client.get(f"/api/v1/containers/{container['id']}/content-units")

    assert response.status_code == 200
    assert response.json() == []


def test_internal_anime_detail_exposes_anilist_metadata_as_source_facts(
    client: TestClient, ingested_anime: tuple[str, str]
) -> None:
    series_id, _ = ingested_anime

    body = client.get(f"/api/v1/works/{series_id}/internal").json()

    assert body["source"] == "anilist"
    assert body["extra_metadata"]["anilist"]["genres"]
    assert body["extra_metadata"]["structure"]["content_units_available"] is False
    assert body["extra_metadata"]["provenance"]["adapter"] == "anime.anilist"


def test_entities_endpoint_returns_characters(
    client: TestClient, ingested_anime: tuple[str, str]
) -> None:
    series_id, _ = ingested_anime

    response = client.get(f"/api/v1/works/{series_id}/entities")

    assert response.status_code == 200
    entities = response.json()
    assert any(e["name"] == "Spike Spiegel" for e in entities)
    assert all(e["entity_type"] == "character" for e in entities)


def test_relationships_endpoint_marks_edges_as_source_provided(
    client: TestClient, ingested_anime: tuple[str, str]
) -> None:
    series_id, movie_id = ingested_anime

    response = client.get(f"/api/v1/works/{series_id}/relationships")

    assert response.status_code == 200
    edges = response.json()
    assert edges
    edge = next(e for e in edges if e["object_id"] == movie_id)
    assert edge["predicate"] == "side_story"
    assert edge["source"] == "source"
    assert edge["method"] == "anilist_relation"
    # Absence of a score is what distinguishes a stated fact from a measurement.
    assert edge["score"] is None and edge["confidence"] is None
    assert edge["object_title"]


def test_literature_work_has_no_entities_or_relationships(
    client: TestClient, ingested_work: str
) -> None:
    """The endpoints work for both domains; literature simply has none yet."""
    assert client.get(f"/api/v1/works/{ingested_work}/entities").json() == []
    assert client.get(f"/api/v1/works/{ingested_work}/relationships").json() == []


# --- text tier and provenance through the API ----------------------------


def test_literature_content_units_are_primary_with_no_text_source(
    client: TestClient, ingested_work: str
) -> None:
    containers = client.get(f"/api/v1/works/{ingested_work}/containers").json()
    chapter = next(c for c in containers if c["container_type"] == "chapter")

    units = client.get(f"/api/v1/containers/{chapter['id']}/content-units").json()

    assert units
    for unit in units:
        assert unit["text_tier"] == "primary"
        assert unit["text_source"] is None


@pytest.fixture
def anime_with_concepts(ingested_anime: tuple[str, str]) -> Iterator[str]:
    """Populate work concepts for the ingested anime, then remove them.

    The vocabulary rows themselves are left in place: they are controlled
    reference data, like the seeded domains, and `ensure_vocabulary` is
    idempotent. Only the per-work associations are cleaned up, and the
    work's own CASCADE would remove them anyway.
    """
    series_id, _ = ingested_anime

    async def populate() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine)() as session:
                await ensure_vocabulary(session)
                work = await session.get(Work, uuid.UUID(series_id))
                await apply_source_labels(session, work=work, labels=anilist_labels(work))
                await session.commit()
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine)() as session:
                await session.execute(
                    text("DELETE FROM work_concepts WHERE work_id = :wid"),
                    {"wid": uuid.UUID(series_id)},
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(populate())
    try:
        yield series_id
    finally:
        asyncio.run(cleanup())

# --- work-level concepts -------------------------------------------------


def test_concepts_endpoint_returns_attributed_associations(
    client: TestClient, anime_with_concepts: str
) -> None:
    """Every concept says where it came from, so none reads as a bare fact."""
    series_id = anime_with_concepts

    response = client.get(f"/api/v1/works/{series_id}/concepts")
    assert response.status_code == 200

    body = response.json()
    assert body, "an AniList work with genres and tags should have concepts"
    for entry in body:
        assert entry["source"] == "source"
        assert entry["method"] in {"anilist_genre", "anilist_tag", "gutenberg_subject"}
        assert entry["slug"] and entry["name"]
        assert entry["concept_type"] in {"theme", "motif", "genre"}
        assert entry["confidence"] is None or 0.0 <= entry["confidence"] <= 1.0


def test_concepts_endpoint_exposes_no_internal_provenance(
    client: TestClient, anime_with_concepts: str
) -> None:
    """Raw supporting labels are ingestion debugging, not reader-facing."""
    series_id = anime_with_concepts

    body = client.get(f"/api/v1/works/{series_id}/concepts").json()

    assert body
    for entry in body:
        assert set(entry) == {
            "slug",
            "name",
            "concept_type",
            "description",
            "source",
            "method",
            "confidence",
        }
    raw = client.get(f"/api/v1/works/{series_id}/concepts").text
    for internal in ("supporting_labels", "text_content", "embedding", "content_unit"):
        assert internal not in raw


def test_concepts_endpoint_orders_ranked_before_unranked(
    client: TestClient, anime_with_concepts: str
) -> None:
    """A source that stated no relevance is not sorted as a low one."""
    series_id = anime_with_concepts

    body = client.get(f"/api/v1/works/{series_id}/concepts").json()
    confidences = [entry["confidence"] for entry in body]
    ranked = [value for value in confidences if value is not None]

    assert confidences[: len(ranked)] == ranked
    assert ranked == sorted(ranked, reverse=True)


def test_concepts_endpoint_404s_for_an_unknown_work(client: TestClient) -> None:
    response = client.get("/api/v1/works/00000000-0000-0000-0000-000000000000/concepts")
    assert response.status_code == 404


def test_concepts_endpoint_needs_no_authentication(
    client: TestClient, anime_with_concepts: str
) -> None:
    """Concepts are canonical and identical for everyone; nothing is per-user."""
    series_id = anime_with_concepts

    assert client.get(f"/api/v1/works/{series_id}/concepts").status_code == 200
