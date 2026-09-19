"""Authentication and the private boundary, checked at the edge.

`test_auth_service.py` covers hashing and session mechanics, and
`test_library_api.py` covers the library's own isolation. This file fills the
gaps that only show up at the HTTP edge, and pins the shapes the frontend now
depends on:

    the 422 body       a short password answers with `detail` as a *list* of
                       objects carrying `ctx.min_length`. The client reads
                       that structure to build "Password must be at least 10
                       characters"; if the shape changes, the message does.

    the boundary       every private surface -- library, preferences,
                       dashboard, feedback, history -- refuses an anonymous
                       caller and shows one reader nothing of another's.

Nothing here weakens a rule to make a client easier to write.
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
from app.models import STATUS_COMPLETED, Work
from app.services.concepts.service import (
    SourceLabel,
    apply_source_labels,
    ensure_vocabulary,
)
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.service import ingest_source_work

FIXTURES = Path(__file__).parent / "fixtures"
EMAIL_DOMAIN = "@auth-boundary.invalid"
SOURCE_IDS = (990001, 990002)
PASSWORD = "a-sufficiently-long-password"
MIN_PASSWORD = 10


@dataclass
class AuthApi:
    client: TestClient
    work_ids: list[str]


@pytest.fixture
def api(database_available: bool) -> Iterator[AuthApi]:
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
                await ensure_vocabulary(session)
                for index, anilist_id in enumerate(SOURCE_IDS):
                    payload = dict(media)
                    payload["id"] = anilist_id
                    payload["title"] = {
                        "romaji": f"Auth Boundary Work {index}",
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
                    await apply_source_labels(
                        session,
                        work=work,
                        labels=[SourceLabel("Psychological", "anilist_tag", rank=88)],
                    )
                    work_ids.append(str(work.id))
                await session.commit()
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine)() as session:
                refs = ", ".join(f"'{value}'" for value in SOURCE_IDS)
                works = (
                    "SELECT id FROM works WHERE source = 'anilist' "
                    f"AND external_ids->>'source_ref' IN ({refs})"
                )
                users = f"SELECT id FROM users WHERE email LIKE '%{EMAIL_DOMAIN}'"
                for statement in (
                    "DELETE FROM user_preference_feedback_events WHERE feedback_id IN "
                    f"(SELECT id FROM user_preference_feedback WHERE user_id IN ({users}))",
                    f"DELETE FROM user_preference_feedback WHERE user_id IN ({users})",
                    "DELETE FROM user_content_events WHERE interaction_id IN "
                    f"(SELECT id FROM user_content_interactions WHERE user_id IN ({users}))",
                    f"DELETE FROM user_content_interactions WHERE user_id IN ({users})",
                    f"DELETE FROM user_sessions WHERE user_id IN ({users})",
                    f"DELETE FROM users WHERE email LIKE '%{EMAIL_DOMAIN}'",
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
            yield AuthApi(client=client, work_ids=work_ids)
        finally:
            asyncio.run(cleanup())


def register(api: AuthApi, name: str, password: str = PASSWORD):
    return api.client.post(
        "/api/v1/auth/register",
        json={"email": f"{name}{EMAIL_DOMAIN}", "password": password},
    )


def headers_for(api: AuthApi, name: str) -> dict:
    response = register(api, name)
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


PRIVATE_GETS = (
    "/api/v1/auth/me",
    "/api/v1/library",
    "/api/v1/library/summary",
    "/api/v1/preferences",
    "/api/v1/preferences/overview",
    "/api/v1/preferences/dashboard",
    "/api/v1/preferences/feedback",
)


# --- registration ---------------------------------------------------------


def test_1_a_short_password_is_refused_with_a_parseable_body(api: AuthApi) -> None:
    """The exact shape the frontend reads to build its message.

    `detail` is a *list*, not a string. Handing that list to `new Error()` is
    what used to render `[object Object]` in the browser.
    """
    response = register(api, "too-short", password="12345678")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    item = detail[0]
    assert item["type"] == "string_too_short"
    assert item["loc"][-1] == "password"
    assert item["ctx"]["min_length"] == MIN_PASSWORD


def test_2_a_password_of_exactly_the_minimum_is_accepted(api: AuthApi) -> None:
    response = register(api, "exactly-ten", password="a" * MIN_PASSWORD)

    assert response.status_code == 201


def test_3_one_character_short_is_refused(api: AuthApi) -> None:
    """The boundary is where it is documented to be, not near it."""
    assert register(api, "nearly", password="a" * (MIN_PASSWORD - 1)).status_code == 422


def test_4_registration_never_returns_the_password_or_its_hash(
    api: AuthApi,
) -> None:
    body = register(api, "no-leak").text

    assert PASSWORD not in body
    for forbidden in ("password", "scrypt", "hash", "salt"):
        assert forbidden not in body.lower()


def test_5_registration_creates_a_working_session(api: AuthApi) -> None:
    response = register(api, "fresh")
    token = response.json()["access_token"]

    me = api.client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == f"fresh{EMAIL_DOMAIN}"


def test_6_a_missing_field_is_a_parseable_422(api: AuthApi) -> None:
    response = api.client.post(
        "/api/v1/auth/register", json={"email": f"nopass{EMAIL_DOMAIN}"}
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any(item["loc"][-1] == "password" for item in detail)


def test_7_an_unusable_email_is_refused(api: AuthApi) -> None:
    response = api.client.post(
        "/api/v1/auth/register", json={"email": "n", "password": PASSWORD}
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "email"


def test_8_a_duplicate_address_is_a_conflict_with_a_string_detail(
    api: AuthApi,
) -> None:
    register(api, "twice")

    response = register(api, "twice")

    assert response.status_code == 409
    # A string, so a client can show it directly.
    assert isinstance(response.json()["detail"], str)


# --- login ----------------------------------------------------------------


def test_9_login_returns_a_session_for_valid_credentials(api: AuthApi) -> None:
    register(api, "logs-in")

    response = api.client.post(
        "/api/v1/auth/login",
        json={"email": f"logs-in{EMAIL_DOMAIN}", "password": PASSWORD},
    )

    # 200, not 201: logging in creates a session but registers no new account.
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"


def test_10_a_wrong_password_and_an_unknown_account_answer_identically(
    api: AuthApi,
) -> None:
    """Login must not say whether an address is registered."""
    register(api, "exists")

    wrong = api.client.post(
        "/api/v1/auth/login",
        json={"email": f"exists{EMAIL_DOMAIN}", "password": "a-different-password"},
    )
    unknown = api.client.post(
        "/api/v1/auth/login",
        json={"email": f"never-registered{EMAIL_DOMAIN}", "password": PASSWORD},
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_11_a_malformed_login_body_is_refused(api: AuthApi) -> None:
    assert api.client.post("/api/v1/auth/login", json={}).status_code == 422
    assert (
        api.client.post("/api/v1/auth/login", content="not json").status_code == 422
    )


# --- sessions -------------------------------------------------------------


def test_12_a_missing_or_malformed_token_is_401(api: AuthApi) -> None:
    for headers in (
        None,
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer   "},
        {"Authorization": "Basic abcdef"},
        {"Authorization": f"Bearer {uuid.uuid4()}"},
        {"Authorization": "Bearer not-a-real-token"},
    ):
        response = api.client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 401, headers
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_13_logout_revokes_the_token_everywhere(api: AuthApi) -> None:
    headers = headers_for(api, "leaver")
    assert api.client.get("/api/v1/auth/me", headers=headers).status_code == 200

    assert api.client.post("/api/v1/auth/logout", headers=headers).status_code == 204

    for path in PRIVATE_GETS:
        assert api.client.get(path, headers=headers).status_code == 401, path


def test_14_logging_out_twice_is_not_an_error_the_second_time(
    api: AuthApi,
) -> None:
    headers = headers_for(api, "double-leaver")
    api.client.post("/api/v1/auth/logout", headers=headers)

    # The token is already gone, so this is an unauthenticated call.
    assert api.client.post("/api/v1/auth/logout", headers=headers).status_code == 401


def test_15_two_logins_yield_independent_sessions(api: AuthApi) -> None:
    """Revoking one device must not sign the reader out of the other."""
    register(api, "two-devices")
    creds = {"email": f"two-devices{EMAIL_DOMAIN}", "password": PASSWORD}
    first = api.client.post("/api/v1/auth/login", json=creds).json()["access_token"]
    second = api.client.post("/api/v1/auth/login", json=creds).json()["access_token"]

    api.client.post(
        "/api/v1/auth/logout", headers={"Authorization": f"Bearer {first}"}
    )

    assert (
        api.client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {second}"}
        ).status_code
        == 200
    )


# --- the private boundary -------------------------------------------------


def test_16_every_private_surface_refuses_an_anonymous_caller(
    api: AuthApi,
) -> None:
    for path in PRIVATE_GETS:
        response = api.client.get(path)
        assert response.status_code == 401, path
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_17_private_writes_refuse_an_anonymous_caller(api: AuthApi) -> None:
    work_id = api.work_ids[0]

    assert api.client.post("/api/v1/library", json={"work_id": work_id}).status_code == 401
    assert (
        api.client.patch(
            f"/api/v1/library/{work_id}", json={"status": STATUS_COMPLETED}
        ).status_code
        == 401
    )
    assert api.client.delete(f"/api/v1/library/{work_id}").status_code == 401
    assert (
        api.client.post(
            "/api/v1/preferences/feedback",
            json={"concept_slug": "psychological-depth", "feedback": "confirmed"},
        ).status_code
        == 401
    )


def test_18_public_work_surfaces_stay_public(api: AuthApi) -> None:
    work_id = api.work_ids[0]

    for path in (
        "/api/v1/works",
        "/api/v1/works/facets",
        "/api/v1/domains",
        f"/api/v1/works/{work_id}",
        f"/api/v1/works/{work_id}/concepts",
    ):
        assert api.client.get(path).status_code == 200, path


def test_19_anonymous_work_data_carries_no_user_state(api: AuthApi) -> None:
    holder = headers_for(api, "holder")
    work_id = api.work_ids[0]
    api.client.post("/api/v1/library", json={"work_id": work_id}, headers=holder)

    listing = api.client.get("/api/v1/works", params={"page_size": 100}).json()
    single = api.client.get(f"/api/v1/works/{work_id}").json()

    assert single["user_state"] is None
    assert all(item["user_state"] is None for item in listing["items"])


def test_20_user_state_belongs_only_to_the_caller(api: AuthApi) -> None:
    alice = headers_for(api, "alice")
    bob = headers_for(api, "bob")
    work_id = api.work_ids[0]
    api.client.post("/api/v1/library", json={"work_id": work_id}, headers=alice)
    api.client.patch(
        f"/api/v1/library/{work_id}",
        json={"rating": 9, "rating_set": True},
        headers=alice,
    )

    def state_for(headers: dict):
        return api.client.get(f"/api/v1/works/{work_id}", headers=headers).json()[
            "user_state"
        ]

    assert state_for(alice)["rating"] == 9
    assert state_for(bob) is None


def test_21_one_reader_sees_nothing_of_anothers_library(api: AuthApi) -> None:
    alice = headers_for(api, "alice-lib")
    bob = headers_for(api, "bob-lib")
    work_id = api.work_ids[0]
    api.client.post("/api/v1/library", json={"work_id": work_id}, headers=alice)

    assert api.client.get("/api/v1/library", headers=bob).json()["total"] == 0
    assert api.client.get("/api/v1/library/summary", headers=bob).json()["total"] == 0
    # 404, not 403: a 403 would confirm the entry exists.
    assert api.client.get(f"/api/v1/library/{work_id}", headers=bob).status_code == 404
    assert (
        api.client.get(f"/api/v1/library/{work_id}/history", headers=bob).status_code
        == 404
    )


def test_22_one_reader_sees_nothing_of_anothers_preferences(api: AuthApi) -> None:
    alice = headers_for(api, "alice-pref")
    bob = headers_for(api, "bob-pref")
    for work_id, rating in zip(api.work_ids, (9, 10)):
        api.client.post("/api/v1/library", json={"work_id": work_id}, headers=alice)
        api.client.patch(
            f"/api/v1/library/{work_id}",
            json={"status": STATUS_COMPLETED},
            headers=alice,
        )
        api.client.patch(
            f"/api/v1/library/{work_id}",
            json={"rating": rating, "rating_set": True},
            headers=alice,
        )

    alice_profile = api.client.get("/api/v1/preferences", headers=alice).json()
    bob_profile = api.client.get("/api/v1/preferences", headers=bob).json()

    assert alice_profile["rating_context"]["rating_count"] == 2
    assert bob_profile["rating_context"]["rating_count"] == 0
    assert bob_profile["concepts"] == []
    assert (
        api.client.get("/api/v1/preferences/dashboard", headers=bob).json()["summary"][
            "rated_works"
        ]
        == 0
    )


def test_23_one_reader_sees_nothing_of_anothers_feedback(api: AuthApi) -> None:
    alice = headers_for(api, "alice-fb")
    bob = headers_for(api, "bob-fb")
    api.client.post(
        "/api/v1/preferences/feedback",
        json={"concept_slug": "psychological-depth", "feedback": "confirmed"},
        headers=alice,
    )

    assert len(api.client.get("/api/v1/preferences/feedback", headers=alice).json()["items"]) == 1
    assert api.client.get("/api/v1/preferences/feedback", headers=bob).json()["items"] == []
    assert (
        api.client.get(
            "/api/v1/preferences/feedback/psychological-depth", headers=bob
        ).json()["current"]
        is None
    )


def test_24_one_reader_cannot_write_to_anothers_entry(api: AuthApi) -> None:
    alice = headers_for(api, "alice-write")
    bob = headers_for(api, "bob-write")
    work_id = api.work_ids[0]
    api.client.post("/api/v1/library", json={"work_id": work_id}, headers=alice)

    assert (
        api.client.patch(
            f"/api/v1/library/{work_id}",
            json={"status": STATUS_COMPLETED},
            headers=bob,
        ).status_code
        == 404
    )
    assert api.client.delete(f"/api/v1/library/{work_id}", headers=bob).status_code == 404

    state = api.client.get(f"/api/v1/library/{work_id}", headers=alice).json()[
        "user_state"
    ]
    assert state["status"] == "planned"
    assert state["in_library"] is True


def test_25_no_private_route_takes_a_user_identifier(api: AuthApi) -> None:
    """The isolation rule, asserted on the routes rather than on responses."""
    from app.main import app

    private = ("/api/v1/library", "/api/v1/preferences", "/api/v1/auth/me")
    for route in app.routes:
        path = getattr(route, "path", "")
        if not any(path.startswith(prefix) for prefix in private):
            continue
        assert "user_id" not in path, path
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for parameter in dependant.query_params:
            assert "user" not in parameter.name, f"{path}: {parameter.name}"


def test_26_a_user_id_in_a_body_is_ignored(api: AuthApi) -> None:
    alice = headers_for(api, "alice-spoof")
    bob = headers_for(api, "bob-spoof")
    alice_id = api.client.get("/api/v1/auth/me", headers=alice).json()["id"]
    work_id = api.work_ids[0]

    api.client.post(
        "/api/v1/library",
        json={"work_id": work_id, "user_id": alice_id},
        headers=bob,
    )

    # The entry belongs to whoever held the token, not to whoever was named.
    assert api.client.get("/api/v1/library", headers=alice).json()["total"] == 0
    assert api.client.get("/api/v1/library", headers=bob).json()["total"] == 1
