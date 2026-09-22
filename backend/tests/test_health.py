"""The health endpoint: what a deployment platform is told, and what it is not.

Two things under test. That the endpoint answers at all when a backing service
is down -- a health check that 500s is worse than useless, because a platform
reads it as a dead process and restarts a perfectly working one. And that it
says nothing a stranger should not hear: it is reachable by anyone who can
reach the API, so no configuration, connection string or error text may reach
the body.
"""

from fastapi.testclient import TestClient

SERVICE_STATES = ("connected", "unavailable")


def test_root_health_returns_200(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert body["database"] in SERVICE_STATES
    assert body["redis"] in SERVICE_STATES
    assert "version" in body


def test_versioned_health_matches_root(client: TestClient) -> None:
    root = client.get("/health").json()
    versioned = client.get("/api/v1/health").json()

    assert root.keys() == versioned.keys()
    assert versioned["database"] in SERVICE_STATES
    assert versioned["redis"] in SERVICE_STATES


def test_health_reports_each_backing_service_separately(
    client: TestClient, database_available: bool
) -> None:
    """One unavailable service must not mask the other.

    Each check is its own round trip with its own guard, so "Postgres is up
    and Redis is not" is a state the endpoint can express -- which is the only
    reason to report two fields rather than one.
    """
    body = client.get("/health").json()

    assert set(body) == {"status", "database", "redis", "version"}
    assert body["database"] == ("connected" if database_available else "unavailable")
    # `status` is the conjunction, so it cannot be "ok" while either is not.
    assert body["status"] == (
        "ok" if body["database"] == "connected" and body["redis"] == "connected" else "degraded"
    )


def test_health_reveals_nothing_about_the_configuration(client: TestClient) -> None:
    """No host, no password, no driver, no path, no traceback."""
    raw = client.get("/health").text.lower()

    for leaked in (
        "postgresql",
        "postgres://",
        "redis://",
        "asyncpg",
        "psycopg2",
        "localhost",
        "password",
        "traceback",
        "sqlalchemy",
        "c:\\",
    ):
        assert leaked not in raw, f"health response leaked {leaked!r}"


def test_health_stays_up_when_redis_is_unreachable(
    client: TestClient, monkeypatch
) -> None:
    """The API serves reads without the queue, so this degrades rather than fails."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    from app.services import health_service

    class Dead:
        def ping(self) -> bool:
            raise RedisConnectionError("no route to host")

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        health_service.Redis, "from_url", classmethod(lambda cls, *a, **k: Dead())
    )

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["redis"] == "unavailable"
    assert body["status"] == "degraded"
    # And the reason stays in the logs rather than in the body.
    assert "no route to host" not in response.text
