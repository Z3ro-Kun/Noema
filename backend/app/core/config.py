from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, sourced from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Multi-Theme API"
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

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
