"""Application configuration, and the checks that stop a bad one shipping.

Every value below has a development default that works on a laptop, which is
what makes `git clone && bootstrap` a working setup. Those same defaults are
wrong in production in ways that fail quietly rather than loudly -- a backend
that starts happily while pointing at `localhost:5432` and accepting CORS
only from `localhost:5173` looks healthy and serves nobody.

So `ENVIRONMENT=production` turns the defaults into errors. `verify_production`
runs at import time in `main` and refuses to start on any of them. It is a
deliberately small list: things that are certainly wrong in production, never
things that are merely unusual.

Two of the checks are about shape rather than about laptops, and they are here
for the same reason. A managed database hands out a connection string that
names no driver, and a Postgres URL that works everywhere else in the world
fails in a SQLAlchemy application in a way that reads as a bug in the
application. Catching it at startup, by name, costs one comparison.
"""

import json
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENVIRONMENT_PRODUCTION = "production"
ENVIRONMENT_DEVELOPMENT = "development"
# Exactly two, and an unrecognised value is refused rather than defaulted.
# Every guard in this module hangs off `is_production`, so `ENVIRONMENT=prod`
# would not be a typo with a small consequence: it would silently turn off
# the localhost checks, the CORS checks and the internal-route gate, and the
# deployment would come up looking fine.
ENVIRONMENTS = (ENVIRONMENT_DEVELOPMENT, ENVIRONMENT_PRODUCTION)

# Everything the local defaults point at. A production value containing any of
# these is a value somebody forgot to set.
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
_PLACEHOLDER = "replace-with-local-password"

# The drivers each connection string has to name.
#
# A managed Postgres dashboard hands out `postgresql://user:pass@host/db`, and
# pasting that into either variable is the single most likely first-deployment
# mistake. SQLAlchemy would then pick its default driver -- psycopg2 for the
# async engine, which fails at the first request with an error about greenlets
# rather than about configuration, and asyncpg for Alembic, which fails
# halfway through a migration. Both are worth catching before the process
# binds a port.
_ASYNC_DB_DRIVERS = ("postgresql+asyncpg://",)
_SYNC_DB_DRIVERS = ("postgresql+psycopg2://", "postgresql+psycopg://")
_REDIS_SCHEMES = ("redis://", "rediss://", "unix://")


class ConfigurationError(RuntimeError):
    """The configuration cannot serve the environment it says it is."""


class Settings(BaseSettings):
    """Application configuration, sourced from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Noema API"
    api_v1_prefix: str = "/api/v1"
    environment: str = "development"

    # Placeholder defaults only -- real credentials belong in .env (gitignored),
    # never in tracked source. These are intentionally non-working so a missing
    # .env fails loudly instead of silently connecting somewhere unexpected.
    # Async URL used by the FastAPI app (asyncpg driver).
    database_url: str = "postgresql+asyncpg://noema:replace-with-local-password@localhost:5432/noema"
    # Sync URL used by Alembic migrations (psycopg2 driver).
    sync_database_url: str = (
        "postgresql+psycopg2://noema:replace-with-local-password@localhost:5432/noema"
    )

    redis_url: str = "redis://localhost:6379/0"

    # Dimensionality of stored embeddings. Must match what
    # `embedding_model_name` actually produces and the width of the
    # `embeddings.vector` column; changing it requires a migration.
    embedding_dimensions: int = 768
    # Production embedding model. Promoted from all-MiniLM-L6-v2 (384-d) after
    # the Phase 1F evaluation: 5 of 6 fixed queries improved, with the
    # abstract identity query gaining +0.189 top-1 cosine and returning
    # qualitatively better passages. See docs/architecture.md.
    embedding_model_name: str = "sentence-transformers/all-mpnet-base-v2"

    # Exact origins, never a wildcard: the API is called with credentials, and
    # `Access-Control-Allow-Origin: *` with credentials is both rejected by
    # browsers and a mistake worth failing on rather than shipping. Set
    # `CORS_ORIGINS` to the deployed frontend's origin -- a JSON list, or a
    # comma-separated string, since a hosting dashboard rarely makes JSON easy.
    #
    # `NoDecode` is what makes the comma-separated form actually reach the
    # validator below. Without it pydantic-settings JSON-decodes any complex
    # field *before* validation, so `CORS_ORIGINS=https://noema.example` --
    # the value this file tells a deployer to type -- died in the settings
    # source with `error parsing value for field "cors_origins"`, naming
    # neither the problem nor the fix. Constructing `Settings(cors_origins=…)`
    # in a test never went through that source and so never saw it.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("environment")
    @classmethod
    def _known_environment(cls, value: str) -> str:
        """Refuse a name this module does not recognise.

        Case and surrounding whitespace are forgiven, because `PRODUCTION`
        and `production ` are unambiguously the same word and a hosting
        dashboard adds whitespace on its own. An abbreviation is not: `prod`
        is rejected rather than guessed at, because guessing wrong turns
        every guard in this file off silently.
        """
        name = value.strip().lower()
        if name not in ENVIRONMENTS:
            raise ValueError(
                f"ENVIRONMENT must be one of {', '.join(ENVIRONMENTS)}; "
                "every production guard depends on it"
            )
        return name

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept `a,b` as well as `["a","b"]`.

        A hosting dashboard is a single-line text box. `https://noema.example`
        is what someone types into it, and two origins are separated with a
        comma, not written as JSON. Both forms are read here, and `NoDecode`
        on the field above is what lets the first one arrive at all.

        The JSON branch is parsed rather than passed through, because
        `NoDecode` turns off the decoding that used to do it.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            return json.loads(text)
        return [origin.strip() for origin in text.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == ENVIRONMENT_PRODUCTION

    def production_problems(self) -> list[str]:
        """Everything about this configuration that cannot be right in production.

        Returned rather than raised so a deployment check can print all of
        them at once instead of revealing them one restart at a time. Never
        includes a value -- only the name of the setting at fault, because
        this list ends up in logs.
        """
        problems: list[str] = []

        for name in ("database_url", "sync_database_url", "redis_url"):
            value = getattr(self, name)
            if _PLACEHOLDER in value:
                problems.append(f"{name.upper()} still contains the example password")
            if any(host in value for host in _LOCAL_HOSTS):
                problems.append(f"{name.upper()} points at localhost")

        # The driver, not just the host. A provider's connection string names
        # no driver at all, and the resulting failure happens at the first
        # query rather than at startup, which is the wrong end of the
        # deployment to discover it at.
        if not self.database_url.startswith(_ASYNC_DB_DRIVERS):
            problems.append(
                "DATABASE_URL does not name the async driver; it must begin "
                "postgresql+asyncpg://"
            )
        if not self.sync_database_url.startswith(_SYNC_DB_DRIVERS):
            problems.append(
                "SYNC_DATABASE_URL does not name a sync driver; it must begin "
                "postgresql+psycopg2:// or postgresql+psycopg://"
            )
        if not self.redis_url.startswith(_REDIS_SCHEMES):
            problems.append(
                "REDIS_URL is not a Redis URL; it must begin redis://, rediss:// or unix://"
            )

        if not self.cors_origins:
            problems.append("CORS_ORIGINS is empty, so no browser origin may call the API")
        for origin in self.cors_origins:
            if origin == "*":
                problems.append(
                    "CORS_ORIGINS contains '*', which cannot be combined with credentials"
                )
            elif any(host in origin for host in _LOCAL_HOSTS):
                problems.append("CORS_ORIGINS points at localhost")
            elif not origin.startswith("https://"):
                problems.append("CORS_ORIGINS contains a non-HTTPS origin")

        return problems

    def verify_production(self) -> None:
        """Refuse to run a development configuration in production."""
        if not self.is_production:
            return
        problems = self.production_problems()
        if problems:
            raise ConfigurationError(
                "ENVIRONMENT=production but the configuration is a development one: "
                + "; ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
