"""Auth and library endpoints, end to end through the app.

Unlike the service tests these need *committed* data, because the API reads
through the app's own sessions. Everything committed here is created under
test-only identifiers and deleted afterwards, so the production corpus and
an otherwise-empty users table are both left as they were found.

The isolation tests are the important ones: they exercise the real HTTP
path, where a client controls the token and the work id and nothing else.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from app.services.ingestion.service import ingest_source_work

FIXTURES = Path(__file__).parent / "fixtures"

# Test-only identifiers. The email domain is reserved by RFC 6761 and the
# source_ref is not one any real ingestion uses, so cleanup can key on both
# without any chance of matching production rows.
API_TEST_SOURCE_REF = "library-api-test-work"
API_TEST_EMAIL_DOMAIN = "@library-api.invalid"
PASSWORD = "a-sufficiently-long-password"


@pytest.fixture
async def canonical_work(database_available: bool) -> AsyncIterator[str]:
    """One committed canonical work, removed again afterwards."""
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    source_work = PlainTextLiteratureAdapter(
        text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
        title="The Lantern Keeper (library API test)",
        source_ref=API_TEST_SOURCE_REF,
        author="A Library Test Author",
    ).load()

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            result = await ingest_source_work(session, source_work)
            await session.commit()

        yield str(result.work_id)

        async with factory() as session:
            # User rows first: they reference the work.
            await session.execute(
                text(
                    "DELETE FROM user_content_events WHERE interaction_id IN "
                    "(SELECT id FROM user_content_interactions WHERE work_id = :wid)"
                ),
                {"wid": result.work_id},
            )
            await session.execute(
                text("DELETE FROM user_content_interactions WHERE work_id = :wid"),
                {"wid": result.work_id},
            )
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
            await session.execute(
                text(
                    "DELETE FROM creators WHERE name = :name AND NOT EXISTS "
                    "(SELECT 1 FROM work_creators wc WHERE wc.creator_id = creators.id)"
                ),
                {"name": "A Library Test Author"},
            )
            await session.commit()
    finally:
        await engine.dispose()


@pytest.fixture
def api(database_available: bool) -> Iterator[TestClient]:
    """A client whose test users are deleted afterwards, whatever happened."""
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    from app.main import app

    with TestClient(app) as client:
        try:
            yield client
        finally:
            import asyncio

            async def cleanup() -> None:
                engine = create_async_engine(get_settings().database_url)
                try:
                    async with async_sessionmaker(bind=engine)() as session:
                        await session.execute(
                            text(
                                "DELETE FROM user_content_events WHERE interaction_id IN ("
                                " SELECT i.id FROM user_content_interactions i"
                                " JOIN users u ON u.id = i.user_id"
                                " WHERE u.email LIKE :pattern)"
                            ),
                            {"pattern": f"%{API_TEST_EMAIL_DOMAIN}"},
                        )
                        await session.execute(
                            text(
                                "DELETE FROM user_content_interactions WHERE user_id IN "
                                "(SELECT id FROM users WHERE email LIKE :pattern)"
                            ),
                            {"pattern": f"%{API_TEST_EMAIL_DOMAIN}"},
                        )
                        await session.execute(
                            text(
                                "DELETE FROM user_sessions WHERE user_id IN "
                                "(SELECT id FROM users WHERE email LIKE :pattern)"
                            ),
                            {"pattern": f"%{API_TEST_EMAIL_DOMAIN}"},
                        )
                        await session.execute(
                            text("DELETE FROM users WHERE email LIKE :pattern"),
                            {"pattern": f"%{API_TEST_EMAIL_DOMAIN}"},
                        )
                        await session.commit()
                finally:
                    await engine.dispose()

            asyncio.run(cleanup())


def register(client: TestClient, name: str) -> tuple[str, dict]:
    """Register a test user and return (token, auth headers)."""
    response = client.post(
        "/api/v1/auth/register",
        json={"email": f"{name}{API_TEST_EMAIL_DOMAIN}", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return token, {"Authorization": f"Bearer {token}"}


# --- authentication ------------------------------------------------------


def test_register_returns_a_session_and_never_the_password(api: TestClient) -> None:
    response = api.post(
        "/api/v1/auth/register",
        json={"email": f"alice{API_TEST_EMAIL_DOMAIN}", "password": PASSWORD},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == f"alice{API_TEST_EMAIL_DOMAIN}"
    # No password material in the response, in any form.
    assert "password" not in response.text.lower()


def test_duplicate_registration_is_a_conflict(api: TestClient) -> None:
    register(api, "alice")

    response = api.post(
        "/api/v1/auth/register",
        json={"email": f"ALICE{API_TEST_EMAIL_DOMAIN}", "password": PASSWORD},
    )
    assert response.status_code == 409


def test_short_password_is_rejected(api: TestClient) -> None:
    response = api.post(
        "/api/v1/auth/register",
        json={"email": f"weak{API_TEST_EMAIL_DOMAIN}", "password": "short"},
    )
    assert response.status_code == 422


def test_login_returns_a_working_token(api: TestClient) -> None:
    register(api, "alice")

    response = api.post(
        "/api/v1/auth/login",
        json={"email": f"alice{API_TEST_EMAIL_DOMAIN}", "password": PASSWORD},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]

    me = api.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == f"alice{API_TEST_EMAIL_DOMAIN}"


def test_wrong_password_and_unknown_user_are_indistinguishable(api: TestClient) -> None:
    """Neither response tells an attacker whether the account exists."""
    register(api, "alice")

    wrong = api.post(
        "/api/v1/auth/login",
        json={"email": f"alice{API_TEST_EMAIL_DOMAIN}", "password": "wrong-password"},
    )
    missing = api.post(
        "/api/v1/auth/login",
        json={"email": f"nobody{API_TEST_EMAIL_DOMAIN}", "password": "wrong-password"},
    )

    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-real-token"},
        {"Authorization": "Basic abc123"},
        {"Authorization": "Bearer "},
    ],
)
def test_protected_routes_require_a_valid_bearer_token(
    api: TestClient, headers: dict
) -> None:
    assert api.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert api.get("/api/v1/library", headers=headers).status_code == 401


def test_logout_makes_the_token_stop_working(api: TestClient) -> None:
    token, headers = register(api, "alice")

    assert api.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert api.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_a_token_is_not_accepted_in_the_query_string(api: TestClient) -> None:
    """Tokens in URLs leak into logs and referrers, so that form is not supported."""
    token, _ = register(api, "alice")

    assert api.get(f"/api/v1/auth/me?access_token={token}").status_code == 401


# --- library ------------------------------------------------------------


def test_library_starts_empty_and_is_not_the_corpus(api: TestClient) -> None:
    """A new user's library is empty even though the corpus is not."""
    _, headers = register(api, "alice")

    library = api.get("/api/v1/library", headers=headers)
    assert library.status_code == 200
    # A page envelope since Phase 1Z, so a client can tell "nothing yet" from
    # "nothing on this page".
    assert library.json()["items"] == []
    assert library.json()["total"] == 0

    works = api.get("/api/v1/works")
    assert works.status_code == 200
    assert works.json()["total"] > 0


def test_add_status_rating_and_read_back(api: TestClient, canonical_work: str) -> None:
    _, headers = register(api, "alice")

    added = api.post("/api/v1/library", json={"work_id": canonical_work}, headers=headers)
    assert added.status_code == 201
    assert added.json()["user_state"]["status"] == "planned"
    assert added.json()["user_state"]["rating"] is None
    assert added.json()["work"]["id"] == canonical_work

    updated = api.patch(
        f"/api/v1/library/{canonical_work}",
        json={"status": "completed", "rating": 9},
        headers=headers,
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["user_state"]["status"] == "completed"
    assert body["user_state"]["rating"] == 9
    assert body["user_state"]["completed_at"] is not None
    assert body["user_state"]["times_completed"] == 1


def test_library_response_exposes_no_content_units_or_text(
    api: TestClient, canonical_work: str
) -> None:
    """The raw corpus stays internal; a library row is a reference plus state."""
    _, headers = register(api, "alice")
    api.post("/api/v1/library", json={"work_id": canonical_work}, headers=headers)

    entry = api.get(f"/api/v1/library/{canonical_work}", headers=headers).json()

    assert set(entry["work"]) == {
        "id",
        "title",
        "original_title",
        "domain",
        "synopsis",
        "cover_image_url",
        "genres",
        "concepts",
        "creators",
        "media_format",
        "year",
        "source",
    }
    for forbidden in ("text_content", "content_units", "containers", "embedding"):
        assert forbidden not in api.get("/api/v1/library", headers=headers).text


def test_completing_through_the_api_does_not_set_a_rating(
    api: TestClient, canonical_work: str
) -> None:
    _, headers = register(api, "alice")
    api.post("/api/v1/library", json={"work_id": canonical_work}, headers=headers)

    body = api.patch(
        f"/api/v1/library/{canonical_work}", json={"status": "completed"}, headers=headers
    ).json()

    assert body["user_state"]["status"] == "completed"
    assert body["user_state"]["rating"] is None


def test_abandoning_through_the_api_records_no_rating(
    api: TestClient, canonical_work: str
) -> None:
    _, headers = register(api, "alice")
    api.post("/api/v1/library", json={"work_id": canonical_work}, headers=headers)

    body = api.patch(
        f"/api/v1/library/{canonical_work}", json={"status": "abandoned"}, headers=headers
    ).json()

    assert body["user_state"]["status"] == "abandoned"
    assert body["user_state"]["abandoned_at"] is not None
    assert body["user_state"]["rating"] is None


def test_rating_can_be_cleared_explicitly(api: TestClient, canonical_work: str) -> None:
    """`rating: null` alone is ambiguous, so clearing takes `rating_set`."""
    _, headers = register(api, "alice")
    api.post("/api/v1/library", json={"work_id": canonical_work}, headers=headers)
    api.patch(f"/api/v1/library/{canonical_work}", json={"rating": 7}, headers=headers)

    untouched = api.patch(
        f"/api/v1/library/{canonical_work}", json={"status": "on_hold"}, headers=headers
    ).json()
    assert untouched["user_state"]["rating"] == 7

    cleared = api.patch(
        f"/api/v1/library/{canonical_work}",
        json={"rating": None, "rating_set": True},
        headers=headers,
    ).json()
    assert cleared["user_state"]["rating"] is None


def test_removal_is_soft_and_readding_restores_the_rating(
    api: TestClient, canonical_work: str
) -> None:
    _, headers = register(api, "alice")
    api.post("/api/v1/library", json={"work_id": canonical_work}, headers=headers)
    api.patch(f"/api/v1/library/{canonical_work}", json={"rating": 9}, headers=headers)

    assert api.delete(f"/api/v1/library/{canonical_work}", headers=headers).status_code == 204
    assert api.get("/api/v1/library", headers=headers).json()["items"] == []

    readded = api.post(
        "/api/v1/library", json={"work_id": canonical_work}, headers=headers
    )
    assert readded.status_code == 201
    assert readded.json()["user_state"]["rating"] == 9


def test_event_history_is_available_for_an_entry(
    api: TestClient, canonical_work: str
) -> None:
    _, headers = register(api, "alice")
    api.post("/api/v1/library", json={"work_id": canonical_work}, headers=headers)
    api.patch(
        f"/api/v1/library/{canonical_work}",
        json={"status": "in_progress", "rating": 8},
        headers=headers,
    )

    events = api.get(f"/api/v1/library/{canonical_work}/events", headers=headers)
    assert events.status_code == 200
    assert [e["event_type"] for e in events.json()] == [
        "added",
        "status_changed",
        "rating_changed",
    ]


def test_adding_an_unknown_work_is_404(api: TestClient) -> None:
    _, headers = register(api, "alice")

    response = api.post(
        "/api/v1/library",
        json={"work_id": "00000000-0000-0000-0000-000000000000"},
        headers=headers,
    )
    assert response.status_code == 404


def test_invalid_status_and_rating_are_rejected(
    api: TestClient, canonical_work: str
) -> None:
    _, headers = register(api, "alice")
    api.post("/api/v1/library", json={"work_id": canonical_work}, headers=headers)

    assert (
        api.patch(
            f"/api/v1/library/{canonical_work}", json={"status": "devoured"}, headers=headers
        ).status_code
        == 422
    )
    assert (
        api.patch(
            f"/api/v1/library/{canonical_work}", json={"rating": 11}, headers=headers
        ).status_code
        == 422
    )


def test_statuses_endpoint_publishes_the_vocabulary(api: TestClient) -> None:
    body = api.get("/api/v1/library/statuses").json()

    assert set(body["statuses"]) == {
        "planned",
        "in_progress",
        "on_hold",
        "completed",
        "abandoned",
    }
    assert (body["rating_min"], body["rating_max"]) == (1, 10)


# --- isolation over the real HTTP path -----------------------------------


def test_two_users_hold_independent_state_for_one_work(
    api: TestClient, canonical_work: str
) -> None:
    """One canonical work, two opinions, no copies."""
    _, alice = register(api, "alice")
    _, bob = register(api, "bob")

    api.post("/api/v1/library", json={"work_id": canonical_work}, headers=alice)
    api.patch(
        f"/api/v1/library/{canonical_work}",
        json={"status": "completed", "rating": 9},
        headers=alice,
    )

    api.post("/api/v1/library", json={"work_id": canonical_work}, headers=bob)
    api.patch(
        f"/api/v1/library/{canonical_work}",
        json={"status": "in_progress", "rating": 3},
        headers=bob,
    )

    alice_entry = api.get(f"/api/v1/library/{canonical_work}", headers=alice).json()
    bob_entry = api.get(f"/api/v1/library/{canonical_work}", headers=bob).json()

    assert (alice_entry["user_state"]["status"], alice_entry["user_state"]["rating"]) == ("completed", 9)
    assert (bob_entry["user_state"]["status"], bob_entry["user_state"]["rating"]) == ("in_progress", 3)
    # Same canonical work on both sides.
    assert alice_entry["work"]["id"] == bob_entry["work"]["id"] == canonical_work
    # Byte-identical canonical half, different user halves: one shared work,
    # two opinions. Interaction ids are no longer exposed at all.
    assert alice_entry["work"] == bob_entry["work"]
    assert alice_entry["user_state"] != bob_entry["user_state"]


def test_user_b_cannot_see_user_as_entry(api: TestClient, canonical_work: str) -> None:
    _, alice = register(api, "alice")
    _, bob = register(api, "bob")
    api.post("/api/v1/library", json={"work_id": canonical_work}, headers=alice)

    assert api.get("/api/v1/library", headers=bob).json()["items"] == []
    # 404, not 403: a 403 would confirm the entry exists.
    assert api.get(f"/api/v1/library/{canonical_work}", headers=bob).status_code == 404
    assert (
        api.get(f"/api/v1/library/{canonical_work}/events", headers=bob).status_code == 404
    )


def test_user_b_cannot_modify_or_delete_user_as_entry(
    api: TestClient, canonical_work: str
) -> None:
    _, alice = register(api, "alice")
    _, bob = register(api, "bob")
    api.post("/api/v1/library", json={"work_id": canonical_work}, headers=alice)
    api.patch(f"/api/v1/library/{canonical_work}", json={"rating": 9}, headers=alice)

    assert (
        api.patch(
            f"/api/v1/library/{canonical_work}",
            json={"status": "abandoned", "rating": 1},
            headers=bob,
        ).status_code
        == 404
    )
    assert api.delete(f"/api/v1/library/{canonical_work}", headers=bob).status_code == 404

    survived = api.get(f"/api/v1/library/{canonical_work}", headers=alice).json()
    assert survived["user_state"]["rating"] == 9
    assert survived["user_state"]["status"] == "planned"
    assert survived["user_state"]["removed_at"] is None


def test_a_revoked_token_cannot_touch_the_library(
    api: TestClient, canonical_work: str
) -> None:
    _, headers = register(api, "alice")
    api.post("/api/v1/library", json={"work_id": canonical_work}, headers=headers)
    api.post("/api/v1/auth/logout", headers=headers)

    assert api.get("/api/v1/library", headers=headers).status_code == 401
    assert (
        api.patch(
            f"/api/v1/library/{canonical_work}", json={"rating": 1}, headers=headers
        ).status_code
        == 401
    )
