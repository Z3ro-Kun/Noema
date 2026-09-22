""""Not interested", and the five things it is not.

Noema records how much a reader liked a work, whether an inferred pattern
about them is right, whether they still want something on their shelf, and
whether they stopped consuming it. This is a fifth, and the risk it carries is
not that it fails to suppress -- that part is easy -- but that it leaks. A
dismissal that quietly lowered a concept's evidence would be the product
inferring a dislike from a shrug.

So most of this file is negative: the same reader dismisses the same work, and
the preference overview, the taste dashboard, the rating history, the library
summary, the work page and the catalogue listing are each checked to come back
byte-identical afterwards. Retrieval is covered structurally instead of by
running a model -- the suppression table is imported by exactly one service
module, so there is no path from a dismissal into semantic search at all, and
a new importer fails that test.

Everything committed here is created under test-only identifiers and removed
afterwards.
"""

import asyncio
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import ACTION_NOT_INTERESTED, STATUS_COMPLETED, Work
from app.services.concepts.service import (
    SourceLabel,
    apply_source_labels,
    ensure_vocabulary,
)
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.service import ingest_source_work

FIXTURES = Path(__file__).parent / "fixtures"
FEEDBACK_EMAIL_DOMAIN = "@recommendation-feedback.invalid"
PASSWORD = "a-sufficiently-long-password"

# A block of AniList ids no other suite uses.
#   4 rated "Psychological" works -> an established positive preference
#   4 unrated candidates carrying the same concept -> a shelf to dismiss from
LIKED = (984001, 984002, 984003, 984004)
CANDIDATES = (984011, 984012, 984013, 984014)
ALL_IDS = LIKED + CANDIDATES


@dataclass
class FeedbackApi:
    client: TestClient
    work_ids: dict[int, str]

    def id(self, anilist_id: int) -> str:
        return self.work_ids[anilist_id]


@pytest.fixture
def api(database_available: bool) -> Iterator[FeedbackApi]:
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    from app.main import app

    work_ids: dict[int, str] = {}

    async def seed() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                await ensure_vocabulary(session)
                media = json.loads(
                    (FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8")
                )
                for anilist_id in ALL_IDS:
                    payload = dict(media)
                    payload["id"] = anilist_id
                    payload["title"] = {
                        "romaji": f"Feedback Work {anilist_id}",
                        "english": f"Feedback Work {anilist_id}",
                        "native": f"Feedback Work {anilist_id}",
                    }
                    payload["relations"] = {"edges": []}
                    payload["genres"] = []
                    payload["tags"] = []
                    payload["episodes"] = None
                    payload["streamingEpisodes"] = []
                    result = await ingest_source_work(
                        session, AniListAnimeAdapter(media=payload).load()
                    )
                    work = await session.get(Work, result.work_id)
                    await apply_source_labels(
                        session,
                        work=work,
                        labels=[SourceLabel("Psychological", "anilist_tag", rank=88)],
                    )
                    work_ids[anilist_id] = str(work.id)
                await session.commit()
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine)() as session:
                refs = ", ".join(f"'{value}'" for value in ALL_IDS)
                works = (
                    "SELECT id FROM works WHERE source = 'anilist' "
                    f"AND external_ids->>'source_ref' IN ({refs})"
                )
                users = f"SELECT id FROM users WHERE email LIKE '%{FEEDBACK_EMAIL_DOMAIN}'"
                for statement in (
                    f"DELETE FROM user_recommendation_feedback WHERE user_id IN ({users})",
                    f"DELETE FROM user_recommendation_feedback WHERE work_id IN ({works})",
                    "DELETE FROM user_content_events WHERE interaction_id IN "
                    f"(SELECT id FROM user_content_interactions WHERE user_id IN ({users}))",
                    f"DELETE FROM user_content_interactions WHERE user_id IN ({users})",
                    "DELETE FROM user_content_events WHERE interaction_id IN "
                    f"(SELECT id FROM user_content_interactions WHERE work_id IN ({works}))",
                    f"DELETE FROM user_content_interactions WHERE work_id IN ({works})",
                    f"DELETE FROM work_concepts WHERE work_id IN ({works})",
                    f"DELETE FROM entities WHERE work_id IN ({works})",
                    f"DELETE FROM containers WHERE work_id IN ({works})",
                    f"DELETE FROM work_creators WHERE work_id IN ({works})",
                    f"DELETE FROM user_sessions WHERE user_id IN ({users})",
                    f"DELETE FROM users WHERE email LIKE '%{FEEDBACK_EMAIL_DOMAIN}'",
                    f"DELETE FROM works WHERE id IN ({works})",
                ):
                    await session.execute(text(statement))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    with TestClient(app) as client:
        try:
            yield FeedbackApi(client=client, work_ids=work_ids)
        finally:
            asyncio.run(cleanup())


def register(api: FeedbackApi, name: str) -> dict:
    response = api.client.post(
        "/api/v1/auth/register",
        json={"email": f"{name}{FEEDBACK_EMAIL_DOMAIN}", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def rate(api: FeedbackApi, headers: dict, anilist_id: int, rating: int) -> None:
    work_id = api.id(anilist_id)
    api.client.post("/api/v1/library", json={"work_id": work_id}, headers=headers)
    api.client.patch(
        f"/api/v1/library/{work_id}", json={"status": STATUS_COMPLETED}, headers=headers
    )
    api.client.patch(
        f"/api/v1/library/{work_id}",
        json={"rating": rating, "rating_set": True},
        headers=headers,
    )


def reader(api: FeedbackApi, name: str) -> dict:
    headers = register(api, name)
    for anilist_id in LIKED:
        rate(api, headers, anilist_id, 9)
    return headers


def shelf(api: FeedbackApi, headers: dict) -> list[str]:
    response = api.client.get(
        "/api/v1/recommendations", headers=headers, params={"limit": 50}
    )
    assert response.status_code == 200, response.text
    return [item["work"]["id"] for item in response.json()["recommendations"]]


def dismiss(api: FeedbackApi, headers: dict, work_id: str):
    return api.client.post(
        f"/api/v1/recommendations/{work_id}/feedback",
        json={"action": ACTION_NOT_INTERESTED},
        headers=headers,
    )


# --- 1, 5: it records, and saying it twice says it once -------------------


def test_a_reader_can_mark_a_recommendation_not_interested(api: FeedbackApi) -> None:
    headers = reader(api, "dismisser")
    candidate = api.id(CANDIDATES[0])
    assert candidate in shelf(api, headers)

    response = dismiss(api, headers, candidate)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["work_id"] == candidate
    assert body["action"] == ACTION_NOT_INTERESTED
    assert body["suppressed_from_recommendations"] is True


def test_saying_it_again_is_saying_it_once(api: FeedbackApi) -> None:
    """Idempotent, and the timestamp keeps saying when they decided."""
    headers = reader(api, "repeater")
    candidate = api.id(CANDIDATES[0])

    first = dismiss(api, headers, candidate)
    second = dismiss(api, headers, candidate)

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json() == second.json()

    rows = api.client.get(
        "/api/v1/recommendations", headers=headers, params={"limit": 50}
    )
    assert rows.status_code == 200
    assert candidate not in [item["work"]["id"] for item in rows.json()["recommendations"]]


# --- 2, 3: refusals --------------------------------------------------------


def test_an_anonymous_caller_cannot_dismiss_anything(api: FeedbackApi) -> None:
    response = api.client.post(
        f"/api/v1/recommendations/{api.id(CANDIDATES[0])}/feedback",
        json={"action": ACTION_NOT_INTERESTED},
    )

    assert response.status_code == 401


def test_a_work_that_does_not_exist_is_refused(api: FeedbackApi) -> None:
    """An id silently accepted is a filter that quietly does nothing."""
    headers = reader(api, "ghost")

    response = dismiss(api, headers, str(uuid.uuid4()))

    assert response.status_code == 404


def test_an_unknown_action_is_refused(api: FeedbackApi) -> None:
    headers = reader(api, "unknown-action")

    response = api.client.post(
        f"/api/v1/recommendations/{api.id(CANDIDATES[0])}/feedback",
        json={"action": "hated_it"},
        headers=headers,
    )

    assert response.status_code == 422


# --- 6: it suppresses, and survives a fresh request -----------------------


def test_a_dismissed_work_leaves_the_shelf_and_stays_away(api: FeedbackApi) -> None:
    headers = reader(api, "suppression")
    candidate = api.id(CANDIDATES[0])
    before = shelf(api, headers)
    assert candidate in before

    assert dismiss(api, headers, candidate).status_code == 201

    after = shelf(api, headers)
    assert candidate not in after
    # Everything else is still there: this removes one work, not a concept.
    assert set(after) >= set(before) - {candidate}
    # And again on a fresh request, which is the whole point of persisting it.
    assert candidate not in shelf(api, headers)


def test_the_dismissal_can_be_taken_back(api: FeedbackApi) -> None:
    headers = reader(api, "restorer")
    candidate = api.id(CANDIDATES[0])
    dismiss(api, headers, candidate)
    assert candidate not in shelf(api, headers)

    undo = api.client.delete(
        f"/api/v1/recommendations/{candidate}/feedback", headers=headers
    )

    assert undo.status_code == 204
    assert candidate in shelf(api, headers)


def test_taking_back_something_never_said_is_a_404(api: FeedbackApi) -> None:
    headers = reader(api, "nothing-to-undo")

    response = api.client.delete(
        f"/api/v1/recommendations/{api.id(CANDIDATES[0])}/feedback", headers=headers
    )

    assert response.status_code == 404


def test_dismissing_several_works_removes_several(api: FeedbackApi) -> None:
    headers = reader(api, "several")
    chosen = [api.id(anilist_id) for anilist_id in CANDIDATES[:3]]
    for work_id in chosen:
        assert dismiss(api, headers, work_id).status_code == 201

    remaining = shelf(api, headers)

    assert not (set(chosen) & set(remaining))
    assert api.id(CANDIDATES[3]) in remaining


# --- 4, 12: isolation ------------------------------------------------------


def test_one_readers_dismissal_does_not_reach_another(api: FeedbackApi) -> None:
    mine = reader(api, "isolation-a")
    theirs = reader(api, "isolation-b")
    candidate = api.id(CANDIDATES[0])

    assert dismiss(api, mine, candidate).status_code == 201

    assert candidate not in shelf(api, mine)
    assert candidate in shelf(api, theirs)


def test_one_reader_cannot_withdraw_anothers_dismissal(api: FeedbackApi) -> None:
    """The row is addressed by (session user, work), so there is no handle."""
    mine = reader(api, "owner")
    theirs = reader(api, "intruder")
    candidate = api.id(CANDIDATES[0])
    dismiss(api, mine, candidate)

    attempt = api.client.delete(
        f"/api/v1/recommendations/{candidate}/feedback", headers=theirs
    )

    assert attempt.status_code == 404
    assert candidate not in shelf(api, mine)


def test_no_user_id_parameter_can_redirect_a_dismissal(api: FeedbackApi) -> None:
    mine = reader(api, "param-owner")
    theirs = reader(api, "param-other")
    other_id = api.client.get("/api/v1/auth/me", headers=theirs).json()["id"]
    candidate = api.id(CANDIDATES[0])

    response = api.client.post(
        f"/api/v1/recommendations/{candidate}/feedback",
        json={"action": ACTION_NOT_INTERESTED, "user_id": other_id},
        params={"user_id": other_id},
        headers=mine,
    )

    assert response.status_code == 201
    # It landed on the caller, not on the id they tried to name.
    assert candidate not in shelf(api, mine)
    assert candidate in shelf(api, theirs)


# --- 7-11: what it must not touch -----------------------------------------


def _preference_state(api: FeedbackApi, headers: dict) -> tuple[dict, dict]:
    overview = api.client.get("/api/v1/preferences/overview", headers=headers)
    dashboard = api.client.get("/api/v1/preferences/dashboard", headers=headers)
    assert overview.status_code == 200 and dashboard.status_code == 200
    return overview.json(), dashboard.json()


def test_a_dismissal_changes_no_preference_evidence(api: FeedbackApi) -> None:
    """The failure this table exists to prevent.

    "Do not recommend this" is not "I dislike this", and a shrug must not
    become negative evidence about a concept the reader has said nothing
    about.
    """
    headers = reader(api, "evidence-unchanged")
    before_overview, _ = _preference_state(api, headers)

    dismiss(api, headers, api.id(CANDIDATES[0]))

    after_overview, _ = _preference_state(api, headers)
    assert after_overview == before_overview


def test_a_dismissal_changes_no_taste_dashboard(api: FeedbackApi) -> None:
    headers = reader(api, "dashboard-unchanged")
    _, before = _preference_state(api, headers)

    dismiss(api, headers, api.id(CANDIDATES[0]))

    _, after = _preference_state(api, headers)
    assert after == before


def test_a_dismissal_changes_no_rating_history(api: FeedbackApi) -> None:
    headers = reader(api, "ratings-unchanged")
    before = api.client.get(
        "/api/v1/library", headers=headers, params={"page_size": 50}
    ).json()

    dismiss(api, headers, api.id(CANDIDATES[0]))

    after = api.client.get(
        "/api/v1/library", headers=headers, params={"page_size": 50}
    ).json()
    assert after == before


def test_a_dismissal_changes_no_library_state(api: FeedbackApi) -> None:
    """A dismissed work is not added to the library, removed from it, or marked."""
    headers = reader(api, "library-unchanged")
    candidate = api.id(CANDIDATES[0])
    before = api.client.get("/api/v1/library/summary", headers=headers).json()

    dismiss(api, headers, candidate)

    after = api.client.get("/api/v1/library/summary", headers=headers).json()
    assert after == before
    # And the work itself has no library entry at all.
    entry = api.client.get(f"/api/v1/works/{candidate}", headers=headers)
    assert entry.status_code == 200
    assert entry.json()["user_state"] is None


def test_a_dismissal_changes_no_catalogue_result(api: FeedbackApi) -> None:
    """Suppression is a recommendation concern, not a retrieval one.

    A reader who dismisses a work must still be able to find it -- they said
    "stop offering me this", not "hide it from me".
    """
    headers = reader(api, "catalogue-unchanged")
    candidate = api.id(CANDIDATES[0])
    title = f"Feedback Work {CANDIDATES[0]}"

    before = api.client.get(
        "/api/v1/works", headers=headers, params={"q": title, "page_size": 20}
    )
    assert before.status_code == 200
    assert candidate in [item["work"]["id"] for item in before.json()["items"]]

    dismiss(api, headers, candidate)

    after = api.client.get(
        "/api/v1/works", headers=headers, params={"q": title, "page_size": 20}
    )
    assert after.status_code == 200
    assert after.json() == before.json()


def test_only_the_recommender_reads_the_feedback_table() -> None:
    """Semantic search cannot be affected by something it never reads.

    Asserted structurally rather than by running a model: the suppression
    table is imported by exactly one service module, so there is no path from
    a dismissal into retrieval, the preference engine, the library or the
    dashboard. A new importer is a deliberate decision and fails here.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    importers = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "UserRecommendationFeedback" in path.read_text(encoding="utf-8")
    }

    assert importers == {
        "models/__init__.py",
        "models/recommendation_feedback.py",
        "services/recommendation.py",
    }, sorted(importers)


def test_a_dismissal_leaves_the_work_page_alone(api: FeedbackApi) -> None:
    headers = reader(api, "work-page-unchanged")
    candidate = api.id(CANDIDATES[0])
    before = api.client.get(f"/api/v1/works/{candidate}", headers=headers).json()

    dismiss(api, headers, candidate)

    after = api.client.get(f"/api/v1/works/{candidate}", headers=headers).json()
    assert after == before


# --- 13: still deterministic ----------------------------------------------


def test_the_shelf_stays_deterministic_after_a_dismissal(api: FeedbackApi) -> None:
    headers = reader(api, "determinism")
    dismiss(api, headers, api.id(CANDIDATES[0]))

    runs = [shelf(api, headers) for _ in range(3)]

    assert runs[0] == runs[1] == runs[2]
    assert runs[0]


def test_dismissing_everything_leaves_an_honest_state(api: FeedbackApi) -> None:
    """No popularity fallback appears to refill an emptied shelf."""
    headers = reader(api, "dismiss-all")
    for work_id in shelf(api, headers):
        assert dismiss(api, headers, work_id).status_code == 201

    response = api.client.get(
        "/api/v1/recommendations", headers=headers, params={"limit": 50}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["recommendations"] == []
    assert body["summary"]["state"] == "no_matches"
    assert body["summary"]["established_preferences"] >= 1
