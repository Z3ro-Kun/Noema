"""Which routes exist in production, and which are a development convenience.

A handful of routes return Noema's insides rather than its product: the stored
text of a work, the raw nearest neighbours behind a search with their
distances, the per-concept evidence the taste engine works from. Every one of
them is genuinely useful while building and has no place in a public
deployment.

They are gated by `ENVIRONMENT`, not by a role -- Noema has readers and
nothing else, and inventing an administrator so three routes could be hidden
would be a larger change than the problem deserves. The gate answers 404, the
same answer an unknown path gets, because a 403 confirms something is there.

The tests below are the two halves of that: the gated routes disappear under
production configuration, and every route the product actually uses survives
it. The second half matters more -- a gate that took the library with it would
be worse than no gate.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import require_internal_surface
from app.core.config import ENVIRONMENT_PRODUCTION, Settings, get_settings
from app.main import app, docs_urls

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Everything that exists only to look at the data layer.
INTERNAL_ROUTES = [
    ("GET", "/api/v1/works/00000000-0000-4000-8000-000000000001/internal"),
    ("GET", "/api/v1/works/00000000-0000-4000-8000-000000000001/concepts"),
    ("GET", "/api/v1/containers/00000000-0000-4000-8000-000000000001/content-units"),
    ("GET", "/api/v1/preferences"),
    ("GET", "/api/v1/preferences/psychological-depth"),
    ("POST", "/api/v1/search/semantic"),
]

# Everything a reader's browser actually calls. None of it may be gated.
PRODUCT_ROUTES = [
    ("GET", "/health"),
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/works"),
    ("GET", "/api/v1/works/facets"),
    ("GET", "/api/v1/domains"),
    ("GET", "/api/v1/library/statuses"),
    ("POST", "/api/v1/search/works"),
    ("GET", "/api/v1/preferences/dashboard"),
    ("GET", "/api/v1/preferences/overview"),
    ("GET", "/api/v1/preferences/feedback"),
    ("GET", "/api/v1/recommendations"),
    ("GET", "/api/v1/library"),
    ("GET", "/api/v1/auth/me"),
]


@pytest.fixture
def production(monkeypatch) -> None:
    """Run the app as though `ENVIRONMENT=production`.

    The setting is read through `get_settings`, which is cached, so the cache
    is replaced rather than the environment poked -- otherwise the change
    would or would not take depending on what had already imported.
    """
    base = get_settings()
    produced = base.model_copy(update={"environment": ENVIRONMENT_PRODUCTION})
    monkeypatch.setattr("app.api.deps.get_settings", lambda: produced)


def call(client: TestClient, method: str, path: str):
    if method == "POST":
        return client.post(path, json={"query": "a chase across a city"})
    return client.get(path)


# --- the gate ------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), INTERNAL_ROUTES)
def test_an_inspection_route_does_not_exist_in_production(
    client: TestClient, production, method: str, path: str
) -> None:
    response = call(client, method, path)

    assert response.status_code == 404, f"{method} {path} answered {response.status_code}"


@pytest.mark.parametrize(("method", "path"), INTERNAL_ROUTES)
def test_the_refusal_is_indistinguishable_from_an_unknown_path(
    client: TestClient, production, method: str, path: str
) -> None:
    """A 403 would confirm the route is there. A 404 says nothing."""
    unknown = client.get("/api/v1/no-such-route").json()
    response = call(client, method, path)

    assert response.status_code == 404
    assert response.json() == unknown or response.json() == {"detail": "not found"}
    # And the refusal names nothing about why.
    for leaked in ("environment", "production", "development", "internal", "disabled"):
        assert leaked not in response.text.lower()


@pytest.mark.parametrize(("method", "path"), INTERNAL_ROUTES)
def test_the_same_route_is_reachable_in_development(
    client: TestClient, method: str, path: str
) -> None:
    """The gate is the only thing hiding them, and only in production.

    Not asserting a 200: most of these want a real work id or an account.
    What must be true is that the *gate* is not what answered.
    """
    response = call(client, method, path)

    assert response.status_code != 404 or "not found" not in response.text.lower() or (
        # A genuine "no such work" is a 404 too; it is not the gate's 404.
        "work not found" in response.text.lower()
        or "container not found" in response.text.lower()
        or "concept" in response.text.lower()
    )


# --- the product is untouched -------------------------------------------


@pytest.mark.parametrize(("method", "path"), PRODUCT_ROUTES)
def test_a_product_route_survives_production(
    client: TestClient, production, method: str, path: str
) -> None:
    """401 is fine -- it means the route is there and wants an account.

    404 is not: it would mean the gate took a product surface with it.
    """
    response = call(client, method, path)

    assert response.status_code != 404, f"{method} {path} was gated by mistake"


def test_no_product_route_carries_the_gate(client: TestClient) -> None:
    """Asserted against the router rather than by calling, so a future route
    added to a gated module cannot pick the dependency up unnoticed."""
    gated = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
        for dependency in getattr(route, "dependencies", []) or []
        if getattr(dependency, "dependency", None) is require_internal_surface
    }

    assert gated == {
        ("GET", "/api/v1/works/{work_id}/internal"),
        ("GET", "/api/v1/works/{work_id}/concepts"),
        ("GET", "/api/v1/containers/{container_id}/content-units"),
        ("GET", "/api/v1/preferences"),
        ("GET", "/api/v1/preferences/{concept_slug}"),
        ("POST", "/api/v1/search/semantic"),
    }


# --- the configuration guard --------------------------------------------


def test_a_development_configuration_cannot_claim_to_be_production() -> None:
    """The failure this exists to prevent: a deployment that starts happily
    while pointing at localhost and serving nobody."""
    settings = Settings(environment=ENVIRONMENT_PRODUCTION, cors_origins=["http://localhost:5173"])

    problems = settings.production_problems()

    assert any("CORS_ORIGINS" in problem for problem in problems)
    with pytest.raises(Exception) as raised:
        settings.verify_production()
    assert "production" in str(raised.value)


def test_a_wildcard_origin_is_refused_because_credentials_are_sent() -> None:
    settings = Settings(environment=ENVIRONMENT_PRODUCTION, cors_origins=["*"])

    assert any("'*'" in problem for problem in settings.production_problems())


def test_a_real_production_configuration_passes() -> None:
    settings = Settings(
        environment=ENVIRONMENT_PRODUCTION,
        database_url="postgresql+asyncpg://u:p@db.internal:5432/noema",
        sync_database_url="postgresql+psycopg2://u:p@db.internal:5432/noema",
        redis_url="rediss://cache.internal:6379/0",
        cors_origins="https://noema.example",
    )

    assert settings.production_problems() == []
    settings.verify_production()


def test_origins_may_be_given_as_a_comma_separated_string() -> None:
    """A hosting dashboard rarely makes a JSON array easy to type."""
    settings = Settings(cors_origins="https://a.example, https://b.example")

    assert settings.cors_origins == ["https://a.example", "https://b.example"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://noema.example", ["https://noema.example"]),
        (
            "https://a.example, https://b.example",
            ["https://a.example", "https://b.example"],
        ),
        (
            '["https://a.example","https://b.example"]',
            ["https://a.example", "https://b.example"],
        ),
    ],
)
def test_origins_survive_the_environment_and_not_only_the_constructor(
    monkeypatch, raw: str, expected: list[str]
) -> None:
    """The bug the test above could not see.

    `CORS_ORIGINS` is only ever set as an environment variable in a real
    deployment, and pydantic-settings JSON-decodes a complex field inside the
    *settings source*, before any validator runs. So the comma-separated form
    this project documents and tests worked when handed to `Settings(...)` and
    died when read from the environment, with
    `error parsing value for field "cors_origins"` and nothing about what to
    type instead. `NoDecode` on the field is the fix; this is the test that
    goes through the path production uses.
    """
    monkeypatch.setenv("CORS_ORIGINS", raw)

    assert Settings().cors_origins == expected


# --- a bad configuration has to fail before anything else does ------------


def _boot(**env: str) -> subprocess.CompletedProcess:
    """Import `app.main` in a clean process, the way a deployment starts it."""
    environment = {
        key: value
        for key, value in os.environ.items()
        # A .env in the working directory would supply what this test is
        # deliberately withholding.
        if not key.startswith(("ENVIRONMENT", "DATABASE_", "SYNC_", "REDIS_", "CORS_"))
    }
    environment.update(env)
    environment["PYTHONPATH"] = str(BACKEND_DIR)
    return subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=str(BACKEND_DIR),
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )


PRODUCTION_ENV = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+asyncpg://u:p@db.internal:5432/noema",
    "SYNC_DATABASE_URL": "postgresql+psycopg2://u:p@db.internal:5432/noema",
    "REDIS_URL": "rediss://cache.internal:6379/0",
    "CORS_ORIGINS": "https://noema.example",
}


def test_a_well_formed_production_configuration_starts() -> None:
    """The control. Without it the two tests below could pass for any reason."""
    result = _boot(**PRODUCTION_ENV)

    assert result.returncode == 0, result.stderr


def test_a_driverless_database_url_is_named_rather_than_crashed_on() -> None:
    """The check has to run before the engine, not only before the routes.

    `create_async_engine` is reached while `app.core.db` is imported, which is
    part of importing `app.main` -- so a check that lived only at the bottom
    of `main` never got to speak. What a deployer saw instead was SQLAlchemy's
    "The asyncio extension requires an async driver to be used. The loaded
    'psycopg2' is not async", which is the symptom and not the mistake, and
    sends them looking in the wrong file.
    """
    result = _boot(
        **{
            **PRODUCTION_ENV,
            "DATABASE_URL": "postgresql://u:p@db.internal:5432/noema",
            "SYNC_DATABASE_URL": "postgresql://u:p@db.internal:5432/noema",
        }
    )

    assert result.returncode != 0
    assert "ConfigurationError" in result.stderr
    assert "DATABASE_URL does not name the async driver" in result.stderr
    assert "asyncio extension requires an async driver" not in result.stderr


def test_the_startup_refusal_still_names_no_value() -> None:
    """It goes to a log the moment the deployment fails, so it says nothing."""
    result = _boot(
        **{
            **PRODUCTION_ENV,
            "DATABASE_URL": "postgresql+asyncpg://noema:hunter2@localhost:5432/noema",
        }
    )

    assert result.returncode != 0
    assert "points at localhost" in result.stderr
    assert "hunter2" not in result.stderr


def test_a_providers_connection_string_is_refused_for_naming_no_driver() -> None:
    """The likeliest first-deployment mistake, caught at startup.

    Every managed Postgres dashboard hands out `postgresql://user:pass@host/db`
    and neither variable can use it as given. Left unchecked, the async engine
    silently picks psycopg2 and fails at the first request with an error about
    greenlets; Alembic picks asyncpg and fails partway through a migration.
    Both are a long way from the word "configuration".
    """
    settings = Settings(
        environment=ENVIRONMENT_PRODUCTION,
        database_url="postgresql://u:p@db.internal:5432/noema",
        sync_database_url="postgresql://u:p@db.internal:5432/noema",
        redis_url="rediss://cache.internal:6379/0",
        cors_origins="https://noema.example",
    )

    problems = " ".join(settings.production_problems())

    assert "DATABASE_URL does not name the async driver" in problems
    assert "SYNC_DATABASE_URL does not name a sync driver" in problems


def test_the_two_database_urls_may_not_be_swapped() -> None:
    """Right drivers, wrong way round -- which otherwise fails at run time."""
    settings = Settings(
        environment=ENVIRONMENT_PRODUCTION,
        database_url="postgresql+psycopg2://u:p@db.internal:5432/noema",
        sync_database_url="postgresql+asyncpg://u:p@db.internal:5432/noema",
        redis_url="rediss://cache.internal:6379/0",
        cors_origins="https://noema.example",
    )

    assert len(settings.production_problems()) == 2


def test_psycopg3_is_accepted_for_the_sync_url() -> None:
    """The guard names the drivers that work, not the one already installed."""
    settings = Settings(
        environment=ENVIRONMENT_PRODUCTION,
        database_url="postgresql+asyncpg://u:p@db.internal:5432/noema",
        sync_database_url="postgresql+psycopg://u:p@db.internal:5432/noema",
        redis_url="rediss://cache.internal:6379/0",
        cors_origins="https://noema.example",
    )

    assert settings.production_problems() == []


def test_a_redis_url_that_is_not_a_redis_url_is_refused() -> None:
    settings = Settings(
        environment=ENVIRONMENT_PRODUCTION,
        database_url="postgresql+asyncpg://u:p@db.internal:5432/noema",
        sync_database_url="postgresql+psycopg2://u:p@db.internal:5432/noema",
        redis_url="https://cache.internal:6379",
        cors_origins="https://noema.example",
    )

    assert any("REDIS_URL is not a Redis URL" in p for p in settings.production_problems())


@pytest.mark.parametrize("value", ["prod", "prd", "staging", "live", ""])
def test_an_unrecognised_environment_name_is_refused(value: str) -> None:
    """`ENVIRONMENT=prod` used to be a typo with no error and large effects.

    Every guard in the module hangs off `is_production`, so a name that does
    not match turns off the localhost checks, the CORS checks, the internal
    route gate and the API schema -- and the deployment comes up looking
    perfectly healthy.
    """
    with pytest.raises(Exception) as raised:
        Settings(environment=value)

    assert "ENVIRONMENT" in str(raised.value)


@pytest.mark.parametrize("value", ["production", "PRODUCTION", " development "])
def test_a_recognised_environment_name_survives_its_own_whitespace(value: str) -> None:
    assert Settings(environment=value).environment == value.strip().lower()


# --- the API schema is a development surface too --------------------------


def test_production_serves_no_api_schema() -> None:
    """`/openapi.json` would index the routes the gate answers 404 for."""
    assert docs_urls(is_production=True) == {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }


def test_development_still_serves_the_docs() -> None:
    """They are how the API is read while it is being built."""
    assert docs_urls(is_production=False)["openapi_url"] == "/openapi.json"


def test_the_running_app_uses_that_rule_rather_than_its_own() -> None:
    """Wired, not merely available."""
    settings = get_settings()

    assert app.openapi_url == docs_urls(settings.is_production)["openapi_url"]
    assert app.docs_url == docs_urls(settings.is_production)["docs_url"]


def test_the_problem_list_never_contains_a_secret() -> None:
    """It ends up in logs, so it names settings rather than values."""
    settings = Settings(
        environment=ENVIRONMENT_PRODUCTION,
        database_url="postgresql+asyncpg://noema:hunter2@localhost:5432/noema",
        sync_database_url="postgresql+psycopg2://noema:hunter2@localhost:5432/noema",
    )

    joined = " ".join(settings.production_problems())

    assert "hunter2" not in joined
    assert "noema:" not in joined
