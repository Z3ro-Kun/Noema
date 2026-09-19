from fastapi.testclient import TestClient


def test_root_health_returns_200(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert body["database"] in ("connected", "unavailable")
    assert "version" in body


def test_versioned_health_matches_root(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["database"] in ("connected", "unavailable")


def test_health_degrades_gracefully_without_database(
    client: TestClient, database_available: bool
) -> None:
    """The API must stay up and report status even if Postgres is down."""
    response = client.get("/health")
    body = response.json()

    if database_available:
        assert body == {"status": "ok", "database": "connected", "version": body["version"]}
    else:
        assert body == {"status": "degraded", "database": "unavailable", "version": body["version"]}
