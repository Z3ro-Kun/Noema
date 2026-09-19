"""Phase 1Z: the library as a reader manages it.

Phase 1L stored the interaction model and Phase 1Z puts a product in front of
it. Nothing about what is *stored* changed, so what is under test here is the
reading of it:

    the shelf is what is on it      soft-removed entries keep their rating and
                                    their history, and must never come back as
                                    though they were still held

    reconsumption is just starting  finishing something and starting it again
                                    is two completions on one row, not a
                                    second row and not a reset

    status and rating never touch   completing writes no rating, abandoning
                                    writes no rating, and rating writes no
                                    status. Asserted in both directions.

    history is told, not dumped     the projection speaks in things that
                                    happened. No event ids, no internal event
                                    vocabulary, no before/after pairs.

Four works are seeded under test-only identifiers and removed afterwards.
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
from app.models import (
    STATUS_ABANDONED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_ON_HOLD,
    STATUS_PLANNED,
    STATUSES,
    Work,
)
from app.schemas.library import HISTORY_KINDS
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.service import ingest_source_work

FIXTURES = Path(__file__).parent / "fixtures"
LIBRARY_EMAIL_DOMAIN = "@library-experience.invalid"
LIBRARY_SOURCE_IDS = (989001, 989002, 989003, 989004)
PASSWORD = "a-sufficiently-long-password"

LIBRARY_URL = "/api/v1/library"


@dataclass
class LibraryApi:
    client: TestClient
    work_ids: list[str]


@pytest.fixture
def api(database_available: bool) -> Iterator[LibraryApi]:
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    from app.main import app

    work_ids: list[str] = []

    async def seed() -> None:
        engine = create_async_engine(get_settings().database_url)
        media = json.loads(
            (FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8")
        )
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                for index, anilist_id in enumerate(LIBRARY_SOURCE_IDS):
                    payload = dict(media)
                    payload["id"] = anilist_id
                    payload["title"] = {
                        "romaji": f"Library Test Work {index}",
                        "english": None,
                        "native": None,
                    }
                    payload["relations"] = {"edges": []}
                    payload["genres"] = []
                    payload["tags"] = []
                    result = await ingest_source_work(
                        session, AniListAnimeAdapter(media=payload).load()
                    )
                    work = await session.get(Work, result.work_id)
                    work_ids.append(str(work.id))
                await session.commit()
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine)() as session:
                refs = ", ".join(f"'{value}'" for value in LIBRARY_SOURCE_IDS)
                works = (
                    "SELECT id FROM works WHERE source = 'anilist' "
                    f"AND external_ids->>'source_ref' IN ({refs})"
                )
                users = (
                    f"SELECT id FROM users WHERE email LIKE '%{LIBRARY_EMAIL_DOMAIN}'"
                )
                for statement in (
                    "DELETE FROM user_content_events WHERE interaction_id IN "
                    f"(SELECT id FROM user_content_interactions WHERE user_id IN ({users}))",
                    f"DELETE FROM user_content_interactions WHERE user_id IN ({users})",
                    "DELETE FROM user_preference_feedback_events WHERE feedback_id IN "
                    f"(SELECT id FROM user_preference_feedback WHERE user_id IN ({users}))",
                    f"DELETE FROM user_preference_feedback WHERE user_id IN ({users})",
                    f"DELETE FROM user_sessions WHERE user_id IN ({users})",
                    f"DELETE FROM users WHERE email LIKE '%{LIBRARY_EMAIL_DOMAIN}'",
                    f"DELETE FROM work_concepts WHERE work_id IN ({works})",
                    f"DELETE FROM entities WHERE work_id IN ({works})",
                    f"DELETE FROM containers WHERE work_id IN ({works})",
                    f"DELETE FROM work_creators WHERE work_id IN ({works})",
                    f"DELETE FROM works WHERE id IN ({works})",
                ):
                    await session.execute(text(statement))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    with TestClient(app) as client:
        try:
            yield LibraryApi(client=client, work_ids=work_ids)
        finally:
            asyncio.run(cleanup())


def register(api: LibraryApi, name: str) -> dict:
    response = api.client.post(
        "/api/v1/auth/register",
        json={"email": f"{name}{LIBRARY_EMAIL_DOMAIN}", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def add(api: LibraryApi, headers: dict, work_id: str):
    return api.client.post(LIBRARY_URL, json={"work_id": work_id}, headers=headers)


def patch(api: LibraryApi, headers: dict, work_id: str, **body):
    return api.client.patch(f"{LIBRARY_URL}/{work_id}", json=body, headers=headers)


def library(api: LibraryApi, headers: dict, **params) -> dict:
    response = api.client.get(LIBRARY_URL, params=params or None, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def summary(api: LibraryApi, headers: dict) -> dict:
    response = api.client.get(f"{LIBRARY_URL}/summary", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def history(api: LibraryApi, headers: dict, work_id: str) -> dict:
    response = api.client.get(f"{LIBRARY_URL}/{work_id}/history", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def titles(page: dict) -> list[str]:
    return [item["work"]["title"] for item in page["items"]]


def kinds(entry: dict) -> list[str]:
    return [item["kind"] for item in entry["entries"]]


# --- the shelf -------------------------------------------------------------


def test_1_a_new_library_is_empty_and_says_so(api: LibraryApi) -> None:
    headers = register(api, "newcomer")

    page = library(api, headers)

    assert page["total"] == 0
    assert page["items"] == []
    assert summary(api, headers)["total"] == 0


def test_2_a_summary_names_every_status_even_at_zero(api: LibraryApi) -> None:
    """A tab that vanishes when it empties is worse than one saying so."""
    headers = register(api, "tabs")
    add(api, headers, api.work_ids[0])

    body = summary(api, headers)

    assert set(body["by_status"]) == set(STATUSES)
    assert body["by_status"][STATUS_PLANNED] == 1
    assert body["by_status"][STATUS_COMPLETED] == 0
    assert body["total"] == 1


def test_3_the_summary_counts_each_status(api: LibraryApi) -> None:
    headers = register(api, "counter")
    for work_id, status in zip(
        api.work_ids,
        (STATUS_IN_PROGRESS, STATUS_ON_HOLD, STATUS_COMPLETED, STATUS_ABANDONED),
    ):
        add(api, headers, work_id)
        patch(api, headers, work_id, status=status)

    body = summary(api, headers)

    assert body["by_status"] == {
        STATUS_PLANNED: 0,
        STATUS_IN_PROGRESS: 1,
        STATUS_ON_HOLD: 1,
        STATUS_COMPLETED: 1,
        STATUS_ABANDONED: 1,
    }
    assert body["total"] == 4


def test_4_the_library_can_be_filtered_by_status(api: LibraryApi) -> None:
    headers = register(api, "filterer")
    for work_id, status in zip(api.work_ids, (STATUS_COMPLETED, STATUS_ON_HOLD)):
        add(api, headers, work_id)
        patch(api, headers, work_id, status=status)

    completed = library(api, headers, status=STATUS_COMPLETED)

    assert completed["total"] == 1
    assert len(completed["items"]) == 1
    assert completed["items"][0]["user_state"]["status"] == STATUS_COMPLETED


def test_5_a_filtered_total_counts_the_filter_not_the_library(
    api: LibraryApi,
) -> None:
    """A count that ignores its filter is worse than none: it looks right."""
    headers = register(api, "totals")
    for work_id in api.work_ids:
        add(api, headers, work_id)
    patch(api, headers, api.work_ids[0], status=STATUS_COMPLETED)

    assert library(api, headers)["total"] == 4
    assert library(api, headers, status=STATUS_COMPLETED)["total"] == 1
    assert library(api, headers, status=STATUS_ABANDONED)["total"] == 0


def test_6_an_unknown_status_filter_is_refused(api: LibraryApi) -> None:
    headers = register(api, "bad-filter")

    response = api.client.get(LIBRARY_URL, params={"status": "reading"}, headers=headers)

    assert response.status_code == 422


def test_7_the_library_pages(api: LibraryApi) -> None:
    headers = register(api, "pager")
    for work_id in api.work_ids:
        add(api, headers, work_id)

    seen: list[str] = []
    for number in (1, 2, 3, 4):
        page = library(api, headers, page=number, page_size=1)
        assert page["total"] == 4
        seen.extend(titles(page))

    assert len(set(seen)) == 4
    assert library(api, headers, page=99)["items"] == []


def test_8_ordering_is_most_recently_touched_first(api: LibraryApi) -> None:
    headers = register(api, "recency")
    for work_id in api.work_ids[:3]:
        add(api, headers, work_id)
    # Touch the oldest one; it should come back to the front.
    patch(api, headers, api.work_ids[0], status=STATUS_IN_PROGRESS)

    page = library(api, headers)

    assert page["items"][0]["work"]["id"] == api.work_ids[0]


def test_9_repeated_reads_are_identical(api: LibraryApi) -> None:
    headers = register(api, "stable")
    for work_id in api.work_ids:
        add(api, headers, work_id)

    assert library(api, headers) == library(api, headers)


# --- removal ---------------------------------------------------------------


def test_10_a_removed_work_leaves_the_active_library(api: LibraryApi) -> None:
    headers = register(api, "tidier")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, rating=9, rating_set=True)

    api.client.delete(f"{LIBRARY_URL}/{work_id}", headers=headers)

    page = library(api, headers)
    assert page["total"] == 0
    assert page["items"] == []
    assert summary(api, headers)["total"] == 0
    assert summary(api, headers)["removed"] == 1


def test_11_a_removed_work_keeps_its_rating_and_history(api: LibraryApi) -> None:
    """Someone who rated a book 9 and tidied their shelf still said that."""
    headers = register(api, "keeper")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_COMPLETED)
    patch(api, headers, work_id, rating=9, rating_set=True)
    api.client.delete(f"{LIBRARY_URL}/{work_id}", headers=headers)

    page = library(api, headers, include_removed=True)

    assert page["total"] == 1
    state = page["items"][0]["user_state"]
    assert state["rating"] == 9
    assert state["times_completed"] == 1
    assert state["in_library"] is False


def test_12_re_adding_revives_the_entry_rather_than_starting_over(
    api: LibraryApi,
) -> None:
    headers = register(api, "returner")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_COMPLETED)
    patch(api, headers, work_id, rating=8, rating_set=True)
    api.client.delete(f"{LIBRARY_URL}/{work_id}", headers=headers)

    again = add(api, headers, work_id)

    assert again.status_code == 201
    state = again.json()["user_state"]
    assert state["rating"] == 8, "an earlier rating must survive a round trip"
    assert state["times_completed"] == 1
    assert state["in_library"] is True
    assert kinds(history(api, headers, work_id))[-1] == "returned"


def test_13_adding_something_already_held_is_refused(api: LibraryApi) -> None:
    headers = register(api, "duplicator")
    add(api, headers, api.work_ids[0])

    assert add(api, headers, api.work_ids[0]).status_code == 409
    assert library(api, headers)["total"] == 1


# --- status and rating are independent -------------------------------------


def test_14_completing_writes_no_rating(api: LibraryApi) -> None:
    headers = register(api, "finisher")
    work_id = api.work_ids[0]
    add(api, headers, work_id)

    patch(api, headers, work_id, status=STATUS_COMPLETED)

    state = library(api, headers)["items"][0]["user_state"]
    assert state["status"] == STATUS_COMPLETED
    assert state["rating"] is None, "finishing something is not liking it"


def test_15_abandoning_writes_no_rating(api: LibraryApi) -> None:
    headers = register(api, "quitter")
    work_id = api.work_ids[0]
    add(api, headers, work_id)

    patch(api, headers, work_id, status=STATUS_ABANDONED)

    state = library(api, headers)["items"][0]["user_state"]
    assert state["rating"] is None, "giving up is not a low score"


def test_16_rating_writes_no_status(api: LibraryApi) -> None:
    headers = register(api, "rater")
    work_id = api.work_ids[0]
    add(api, headers, work_id)

    patch(api, headers, work_id, rating=10, rating_set=True)

    state = library(api, headers)["items"][0]["user_state"]
    assert state["rating"] == 10
    assert state["status"] == STATUS_PLANNED, "rating must not imply progress"


def test_17_a_work_can_be_rated_without_being_finished(api: LibraryApi) -> None:
    headers = register(api, "early-rater")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_IN_PROGRESS)

    patch(api, headers, work_id, rating=7, rating_set=True)

    state = library(api, headers)["items"][0]["user_state"]
    assert state["status"] == STATUS_IN_PROGRESS
    assert state["rating"] == 7


def test_18_a_rating_can_be_changed_and_cleared(api: LibraryApi) -> None:
    headers = register(api, "mind-changer")
    work_id = api.work_ids[0]
    add(api, headers, work_id)

    patch(api, headers, work_id, rating=6, rating_set=True)
    patch(api, headers, work_id, rating=9, rating_set=True)
    assert library(api, headers)["items"][0]["user_state"]["rating"] == 9

    patch(api, headers, work_id, rating=None, rating_set=True)
    state = library(api, headers)["items"][0]["user_state"]
    # Unrated is a different fact from a low rating, and is stored as one.
    assert state["rating"] is None
    assert state["status"] == STATUS_PLANNED


def test_19_an_out_of_range_rating_is_refused(api: LibraryApi) -> None:
    headers = register(api, "out-of-range")
    work_id = api.work_ids[0]
    add(api, headers, work_id)

    for value in (0, 11, -1):
        assert patch(api, headers, work_id, rating=value, rating_set=True).status_code == 422
    assert library(api, headers)["items"][0]["user_state"]["rating"] is None


# --- reconsumption ---------------------------------------------------------


def test_20_starting_again_after_finishing_is_a_second_completion(
    api: LibraryApi,
) -> None:
    """Reconsumption is the existing status model, not a new concept."""
    headers = register(api, "rereader")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_IN_PROGRESS)
    patch(api, headers, work_id, status=STATUS_COMPLETED)

    patch(api, headers, work_id, status=STATUS_IN_PROGRESS)
    patch(api, headers, work_id, status=STATUS_COMPLETED)

    state = library(api, headers)["items"][0]["user_state"]
    assert state["times_completed"] == 2
    assert state["times_started"] == 2
    assert state["status"] == STATUS_COMPLETED


def test_21_reconsuming_duplicates_no_canonical_work(api: LibraryApi) -> None:
    headers = register(api, "no-duplicates")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    for status in (STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_IN_PROGRESS, STATUS_COMPLETED):
        patch(api, headers, work_id, status=status)

    page = library(api, headers)

    assert page["total"] == 1
    assert len(page["items"]) == 1


def test_22_reconsuming_creates_no_rating_and_keeps_the_old_one(
    api: LibraryApi,
) -> None:
    headers = register(api, "unrated-reread")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_COMPLETED)
    patch(api, headers, work_id, rating=8, rating_set=True)

    patch(api, headers, work_id, status=STATUS_IN_PROGRESS)

    state = library(api, headers)["items"][0]["user_state"]
    assert state["rating"] == 8, "reconsuming must not invent or clear a rating"


def test_23_a_rating_changed_after_reconsuming_is_just_a_new_rating(
    api: LibraryApi,
) -> None:
    headers = register(api, "reappraiser")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_COMPLETED)
    patch(api, headers, work_id, rating=6, rating_set=True)
    patch(api, headers, work_id, status=STATUS_IN_PROGRESS)
    patch(api, headers, work_id, status=STATUS_COMPLETED)
    patch(api, headers, work_id, rating=9, rating_set=True)

    entry = history(api, headers, work_id)

    assert entry["rating"] == 9
    assert entry["times_completed"] == 2
    ratings = [item["rating"] for item in entry["entries"] if item["kind"] == "rated"]
    assert ratings == [6, 9], "both opinions are on record"


# --- history ---------------------------------------------------------------


def test_24_history_reads_as_things_that_happened(api: LibraryApi) -> None:
    headers = register(api, "storyteller")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_IN_PROGRESS)
    patch(api, headers, work_id, status=STATUS_COMPLETED)
    patch(api, headers, work_id, rating=9, rating_set=True)
    patch(api, headers, work_id, status=STATUS_IN_PROGRESS)

    assert kinds(history(api, headers, work_id)) == [
        "added",
        "started",
        "completed",
        "rated",
        "restarted",
    ]


def test_25_a_first_start_is_not_a_restart(api: LibraryApi) -> None:
    headers = register(api, "first-timer")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_IN_PROGRESS)

    assert kinds(history(api, headers, work_id)) == ["added", "started"]


def test_26_history_is_oldest_first_and_stable(api: LibraryApi) -> None:
    headers = register(api, "chronology")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_ON_HOLD)
    patch(api, headers, work_id, status=STATUS_ABANDONED)

    first = history(api, headers, work_id)
    assert kinds(first) == ["added", "paused", "abandoned"]
    assert history(api, headers, work_id) == first


def test_27_only_a_rating_entry_carries_a_number(api: LibraryApi) -> None:
    headers = register(api, "numbers")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_COMPLETED)
    patch(api, headers, work_id, rating=7, rating_set=True)

    entries = history(api, headers, work_id)["entries"]

    for item in entries:
        if item["kind"] == "rated":
            assert item["rating"] == 7
        else:
            assert item["rating"] is None


def test_28_clearing_a_rating_is_its_own_kind(api: LibraryApi) -> None:
    headers = register(api, "clearer")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, rating=5, rating_set=True)
    patch(api, headers, work_id, rating=None, rating_set=True)

    assert kinds(history(api, headers, work_id)) == ["added", "rated", "rating_cleared"]


def test_29_history_exposes_no_event_internals(api: LibraryApi) -> None:
    headers = register(api, "no-internals")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_COMPLETED)
    patch(api, headers, work_id, rating=9, rating_set=True)

    payload = json.dumps(history(api, headers, work_id))

    for forbidden in (
        "event_type",
        "status_changed",
        "rating_changed",
        "status_before",
        "status_after",
        "rating_before",
        "interaction_id",
        "extra_metadata",
    ):
        assert forbidden not in payload, f"{forbidden} leaked into history"


def test_30_every_history_kind_is_in_the_declared_vocabulary(
    api: LibraryApi,
) -> None:
    headers = register(api, "vocabulary")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    for body in (
        {"status": STATUS_IN_PROGRESS},
        {"status": STATUS_ON_HOLD},
        {"status": STATUS_COMPLETED},
        {"rating": 9, "rating_set": True},
        {"status": STATUS_IN_PROGRESS},
        {"status": STATUS_ABANDONED},
        {"rating": None, "rating_set": True},
    ):
        patch(api, headers, work_id, **body)
    api.client.delete(f"{LIBRARY_URL}/{work_id}", headers=headers)
    add(api, headers, work_id)

    for kind in kinds(history(api, headers, work_id)):
        assert kind in HISTORY_KINDS, kind


def test_31_history_counts_match_the_stored_state(api: LibraryApi) -> None:
    """The figure a reader sees is the one the preference engine reads."""
    headers = register(api, "consistent")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    for status in (STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_IN_PROGRESS, STATUS_COMPLETED):
        patch(api, headers, work_id, status=status)

    entry = history(api, headers, work_id)
    state = library(api, headers)["items"][0]["user_state"]

    assert entry["times_completed"] == state["times_completed"] == 2
    assert entry["times_started"] == state["times_started"] == 2
    assert entry["current_status"] == state["status"]


def test_32_history_for_a_work_never_held_is_404(api: LibraryApi) -> None:
    headers = register(api, "stranger-to-it")

    response = api.client.get(
        f"{LIBRARY_URL}/{api.work_ids[0]}/history", headers=headers
    )

    assert response.status_code == 404


# --- isolation -------------------------------------------------------------


def test_33_one_reader_never_sees_another_readers_library(api: LibraryApi) -> None:
    owner = register(api, "owner")
    stranger = register(api, "stranger")
    add(api, owner, api.work_ids[0])
    patch(api, owner, api.work_ids[0], rating=9, rating_set=True)

    assert library(api, stranger)["total"] == 0
    assert summary(api, stranger)["total"] == 0
    assert (
        api.client.get(
            f"{LIBRARY_URL}/{api.work_ids[0]}/history", headers=stranger
        ).status_code
        == 404
    )


def test_34_one_reader_cannot_change_another_readers_entry(api: LibraryApi) -> None:
    owner = register(api, "holder")
    attacker = register(api, "meddler")
    work_id = api.work_ids[0]
    add(api, owner, work_id)

    assert patch(api, attacker, work_id, status=STATUS_COMPLETED).status_code == 404
    assert api.client.delete(f"{LIBRARY_URL}/{work_id}", headers=attacker).status_code == 404
    assert library(api, owner)["items"][0]["user_state"]["status"] == STATUS_PLANNED


def test_35_anonymous_callers_reach_no_library_surface(api: LibraryApi) -> None:
    for path in (LIBRARY_URL, f"{LIBRARY_URL}/summary", f"{LIBRARY_URL}/{api.work_ids[0]}/history"):
        response = api.client.get(path)
        assert response.status_code == 401, path
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_36_the_library_routes_accept_no_user_identifier() -> None:
    from app.main import app

    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith(LIBRARY_URL):
            continue
        assert "user" not in path
        names = {p.name for p in route.dependant.query_params}
        assert not any("user" in name for name in names), path


def test_37_a_user_id_parameter_is_ignored(api: LibraryApi) -> None:
    owner = register(api, "victim-of-1z")
    attacker = register(api, "hopeful")
    add(api, owner, api.work_ids[0])
    owner_id = api.client.get("/api/v1/auth/me", headers=owner).json()["id"]

    page = api.client.get(
        LIBRARY_URL, params={"user_id": owner_id}, headers=attacker
    ).json()

    assert page["total"] == 0


# --- the product boundary --------------------------------------------------


def test_38_the_library_exposes_no_corpus_internals(api: LibraryApi) -> None:
    headers = register(api, "boundary")
    for work_id in api.work_ids:
        add(api, headers, work_id)

    payload = json.dumps(library(api, headers))

    for forbidden in (
        "content_unit",
        "text_content",
        "embedding",
        "vector",
        "extra_metadata",
        "external_ids",
        "adapter",
        "passage",
    ):
        assert forbidden not in payload, f"{forbidden} leaked into the library"


def test_39_the_library_carries_no_preference_internals(api: LibraryApi) -> None:
    """A library entry says what the reader did, never what Noema concluded."""
    headers = register(api, "no-scores")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_COMPLETED)
    patch(api, headers, work_id, rating=9, rating_set=True)

    payload = json.dumps(
        {
            "page": library(api, headers),
            "summary": summary(api, headers),
            "history": history(api, headers, work_id),
        }
    )

    for forbidden in (
        "preference_evidence",
        "confidence",
        "normalized_rating",
        "baseline",
        "taste",
        "feedback",
    ):
        assert forbidden not in payload, f"{forbidden} leaked into the library"


def test_40_library_writes_do_not_touch_preference_feedback(api: LibraryApi) -> None:
    """The 1X channel stays separate: rating a work states no verdict."""
    headers = register(api, "separate-channels")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_COMPLETED)
    patch(api, headers, work_id, rating=10, rating_set=True)

    feedback = api.client.get("/api/v1/preferences/feedback", headers=headers).json()

    assert feedback["items"] == []


def test_41_the_raw_event_log_remains_available_for_development(
    api: LibraryApi,
) -> None:
    """`/history` is the product view; `/events` is not removed by it."""
    headers = register(api, "developer")
    work_id = api.work_ids[0]
    add(api, headers, work_id)
    patch(api, headers, work_id, status=STATUS_COMPLETED)

    events = api.client.get(f"{LIBRARY_URL}/{work_id}/events", headers=headers).json()

    assert [event["event_type"] for event in events] == ["added", "status_changed"]
