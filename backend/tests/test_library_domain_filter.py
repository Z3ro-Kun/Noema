"""Phase 1AC: narrowing a library by medium.

The library already filtered by status and paged server-side. This adds the
other axis a reader actually thinks in -- "the anime I finished", "the books
I am part-way through" -- and the thing under test is that it composes rather
than competes:

    domain alone        only that medium
    domain + status     both, in one query
    neither             exactly what it returned before

Filtering is the server's. A client that downloaded a whole library and hid
part of it would pass a naive test and fail the first reader with a thousand
entries, so these assert on `total` as well as on the page: a count that
ignores the filter is worse than no count, because it looks right.

The fixture seeds its own works in two domains rather than reusing the
library-experience fixture, which is anime-only and shared by twenty tests
that have no business changing shape for this one.
"""

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import STATUS_COMPLETED, STATUS_IN_PROGRESS, Work
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.manga import AniListMangaAdapter
from app.services.ingestion.service import ingest_source_work

FIXTURES = Path(__file__).parent / "fixtures"
EMAIL_DOMAIN = "@library-domain.invalid"
PASSWORD = "a-sufficiently-long-password"
LIBRARY_URL = "/api/v1/library"

# Test-only AniList ids, far from anything real.
ANIME_IDS = (988101, 988102)
MANGA_IDS = (988201, 988202, 988203)


@dataclass
class DomainApi:
    client: TestClient
    anime: list[str]
    manhwa: list[str]


@pytest.fixture
def api(database_available: bool) -> Iterator[DomainApi]:
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    from app.main import app

    anime: list[str] = []
    manhwa: list[str] = []

    async def seed() -> None:
        engine = create_async_engine(get_settings().database_url)
        anime_media = json.loads(
            (FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8")
        )
        manga_media = json.loads(
            (FIXTURES / "anilist_manga_vinland_saga.json").read_text(encoding="utf-8")
        )
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                for index, anilist_id in enumerate(ANIME_IDS):
                    payload = dict(anime_media)
                    payload["id"] = anilist_id
                    payload["title"] = {
                        "romaji": f"Domain Anime {index}",
                        "english": None,
                        "native": None,
                    }
                    payload["relations"] = {"edges": []}
                    payload["genres"] = []
                    payload["tags"] = []
                    result = await ingest_source_work(
                        session, AniListAnimeAdapter(media=payload).load()
                    )
                    anime.append(str((await session.get(Work, result.work_id)).id))

                for index, anilist_id in enumerate(MANGA_IDS):
                    payload = dict(manga_media)
                    payload["id"] = anilist_id
                    payload["title"] = {
                        "romaji": f"Domain Manga {index}",
                        "english": None,
                        "native": None,
                    }
                    payload["relations"] = {"edges": []}
                    payload["genres"] = []
                    payload["tags"] = []
                    result = await ingest_source_work(
                        session, AniListMangaAdapter(media=payload).load()
                    )
                    manhwa.append(str((await session.get(Work, result.work_id)).id))
                await session.commit()
        finally:
            await engine.dispose()

    async def teardown() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine)() as session:
                # Ordered by dependency: canonical content is never cascaded
                # into from a user's library, so it is dismantled explicitly.
                users = "SELECT id FROM users WHERE email LIKE :pattern"
                for statement in (
                    f"DELETE FROM user_content_events WHERE interaction_id IN "
                    f"(SELECT id FROM user_content_interactions WHERE user_id IN ({users}))",
                    f"DELETE FROM user_content_interactions WHERE user_id IN ({users})",
                    f"DELETE FROM user_preference_feedback_events WHERE feedback_id IN "
                    f"(SELECT id FROM user_preference_feedback WHERE user_id IN ({users}))",
                    f"DELETE FROM user_preference_feedback WHERE user_id IN ({users})",
                    f"DELETE FROM user_sessions WHERE user_id IN ({users})",
                    "DELETE FROM users WHERE email LIKE :pattern",
                ):
                    await session.execute(text(statement), {"pattern": f"%{EMAIL_DOMAIN}"})

                ids = [*anime, *manhwa]
                for statement in (
                    "DELETE FROM work_concepts WHERE work_id = ANY(:ids)",
                    "DELETE FROM entities WHERE work_id = ANY(:ids)",
                    "DELETE FROM content_units WHERE container_id IN "
                    "(SELECT id FROM containers WHERE work_id = ANY(:ids))",
                    "DELETE FROM containers WHERE work_id = ANY(:ids)",
                    "DELETE FROM work_creators WHERE work_id = ANY(:ids)",
                    "DELETE FROM relationships WHERE subject_id = ANY(:ids) OR object_id = ANY(:ids)",
                    "DELETE FROM works WHERE id = ANY(:ids)",
                ):
                    await session.execute(text(statement), {"ids": ids})
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    with TestClient(app) as client:
        yield DomainApi(client=client, anime=anime, manhwa=manhwa)
    asyncio.run(teardown())


def register(api: DomainApi, name: str) -> dict:
    response = api.client.post(
        "/api/v1/auth/register",
        json={"email": f"{name}{EMAIL_DOMAIN}", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def add(api: DomainApi, headers: dict, work_id: str, status: str | None = None) -> None:
    assert api.client.post(
        LIBRARY_URL, json={"work_id": work_id}, headers=headers
    ).status_code == 201
    if status:
        assert api.client.patch(
            f"{LIBRARY_URL}/{work_id}", json={"status": status}, headers=headers
        ).status_code == 200


def library(api: DomainApi, headers: dict, **params) -> dict:
    response = api.client.get(LIBRARY_URL, params=params or None, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def domains(page: dict) -> list[str]:
    return [item["work"]["domain"]["slug"] for item in page["items"]]


# --- one axis --------------------------------------------------------------


def test_01_a_domain_filter_returns_only_that_medium(api: DomainApi) -> None:
    headers = register(api, "one-medium")
    for work_id in api.anime:
        add(api, headers, work_id)
    for work_id in api.manhwa:
        add(api, headers, work_id)

    page = library(api, headers, domain="anime")

    assert domains(page) == ["anime"] * len(api.anime)
    assert page["total"] == len(api.anime)


def test_02_the_total_respects_the_filter(api: DomainApi) -> None:
    """A count that ignores the filter looks right and is not."""
    headers = register(api, "counted")
    for work_id in [*api.anime, *api.manhwa]:
        add(api, headers, work_id)

    assert library(api, headers, domain="manhwa")["total"] == len(api.manhwa)
    assert library(api, headers, domain="anime")["total"] == len(api.anime)
    assert library(api, headers)["total"] == len(api.anime) + len(api.manhwa)


def test_03_no_domain_is_every_domain(api: DomainApi) -> None:
    """The unfiltered library is exactly what it was before this existed."""
    headers = register(api, "unfiltered")
    for work_id in [*api.anime, *api.manhwa]:
        add(api, headers, work_id)

    page = library(api, headers)

    assert page["total"] == len(api.anime) + len(api.manhwa)
    assert set(domains(page)) == {"anime", "manhwa"}


def test_04_a_domain_holding_nothing_is_empty_not_wrong(api: DomainApi) -> None:
    headers = register(api, "nothing-there")
    for work_id in api.anime:
        add(api, headers, work_id)

    page = library(api, headers, domain="literature")

    assert page["items"] == []
    assert page["total"] == 0


# --- composing with status -------------------------------------------------


def test_05_domain_and_status_compose(api: DomainApi) -> None:
    headers = register(api, "both-axes")
    add(api, headers, api.anime[0], STATUS_COMPLETED)
    add(api, headers, api.anime[1], STATUS_IN_PROGRESS)
    add(api, headers, api.manhwa[0], STATUS_COMPLETED)

    page = library(api, headers, domain="anime", status=STATUS_COMPLETED)

    assert page["total"] == 1
    assert domains(page) == ["anime"]
    assert page["items"][0]["work"]["id"] == api.anime[0]


def test_06_the_same_status_in_another_medium(api: DomainApi) -> None:
    headers = register(api, "other-medium")
    add(api, headers, api.anime[0], STATUS_COMPLETED)
    add(api, headers, api.manhwa[0], STATUS_COMPLETED)
    add(api, headers, api.manhwa[1], STATUS_COMPLETED)

    page = library(api, headers, domain="manhwa", status=STATUS_COMPLETED)

    assert page["total"] == 2
    assert set(domains(page)) == {"manhwa"}


def test_07_a_combination_matching_nothing_is_empty(api: DomainApi) -> None:
    headers = register(api, "empty-combination")
    add(api, headers, api.anime[0], STATUS_COMPLETED)

    page = library(api, headers, domain="anime", status=STATUS_IN_PROGRESS)

    assert page["items"] == []
    assert page["total"] == 0


def test_08_the_summary_is_not_narrowed_by_domain(api: DomainApi) -> None:
    """The tabs count the whole library, and are meant to.

    `/summary` takes no domain and never has. A reader narrowing to one
    medium is still told how much is in each status overall, which is the
    question the tabs answer.
    """
    headers = register(api, "summary-unchanged")
    add(api, headers, api.anime[0], STATUS_COMPLETED)
    add(api, headers, api.manhwa[0], STATUS_COMPLETED)

    summary = api.client.get(f"{LIBRARY_URL}/summary", headers=headers).json()

    assert summary["by_status"][STATUS_COMPLETED] == 2


# --- the things it must not break ------------------------------------------


def test_09_paging_still_pages_within_a_domain(api: DomainApi) -> None:
    headers = register(api, "paged")
    for work_id in api.manhwa:
        add(api, headers, work_id)

    first = library(api, headers, domain="manhwa", page=1, page_size=2)
    second = library(api, headers, domain="manhwa", page=2, page_size=2)

    assert len(first["items"]) == 2
    assert len(second["items"]) == len(api.manhwa) - 2
    # `total` is everything that matched, not what is on the page.
    assert first["total"] == second["total"] == len(api.manhwa)
    assert set(domains(first)) | set(domains(second)) == {"manhwa"}


def test_10_removed_entries_stay_out_unless_asked_for(api: DomainApi) -> None:
    headers = register(api, "removed-stays-out")
    add(api, headers, api.anime[0])
    add(api, headers, api.anime[1])
    api.client.delete(f"{LIBRARY_URL}/{api.anime[0]}", headers=headers)

    held = library(api, headers, domain="anime")
    with_removed = library(api, headers, domain="anime", include_removed=True)

    assert held["total"] == 1
    assert with_removed["total"] == 2


def test_11_an_unknown_domain_matches_nothing_rather_than_erroring(
    api: DomainApi,
) -> None:
    """The listing filters; it does not validate a vocabulary it does not own."""
    headers = register(api, "unknown-domain")
    add(api, headers, api.anime[0])

    page = library(api, headers, domain="not-a-domain")

    assert page["items"] == []
    assert page["total"] == 0


def test_12_one_readers_filter_cannot_reach_anothers_library(api: DomainApi) -> None:
    alice = register(api, "domain-alice")
    bob = register(api, "domain-bob")
    for work_id in api.anime:
        add(api, alice, work_id)
    add(api, bob, api.manhwa[0])

    assert library(api, alice, domain="manhwa")["total"] == 0
    assert library(api, bob, domain="anime")["total"] == 0
    assert library(api, bob, domain="manhwa")["total"] == 1


def test_13_anonymous_callers_get_nothing(api: DomainApi) -> None:
    assert api.client.get(LIBRARY_URL, params={"domain": "anime"}).status_code == 401
