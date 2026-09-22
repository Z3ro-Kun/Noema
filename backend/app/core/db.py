from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

# Here, and not only in `main`, because this line is the one that fails.
#
# `create_async_engine` below is reached during the import of `app.main`,
# *before* `main` gets to call `verify_production()` itself -- and a
# DATABASE_URL that names no driver dies inside SQLAlchemy with "The asyncio
# extension requires an async driver to be used. The loaded 'psycopg2' is not
# async", which describes the symptom and not the mistake. Verifying
# immediately before the engine is built is what makes the configuration
# error arrive first and name the setting.
#
# Idempotent and cheap: `get_settings` is cached and the check is a handful
# of string comparisons, so `main` calling it again costs nothing.
settings.verify_production()

engine = create_async_engine(settings.database_url, pool_pre_ping=True)

async_session_factory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped database session."""
    async with async_session_factory() as session:
        yield session
