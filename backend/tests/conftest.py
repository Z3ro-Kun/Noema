from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Used as a context manager so the app's lifespan (incl. engine.dispose()
    # on shutdown) actually runs instead of leaking pooled connections.
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def database_available() -> bool:
    """True if the configured Postgres instance is reachable.

    Tests that need a live database (e.g. running migrations) skip
    themselves when it isn't -- this repo's DB only exists once
    `docker compose up db` has been run.
    """
    settings = get_settings()
    engine = create_engine(settings.sync_database_url, connect_args={"connect_timeout": 2})
    try:
        with engine.connect():
            return True
    except OperationalError:
        return False
    finally:
        engine.dispose()


@pytest.fixture
def sync_db_session(database_available: bool) -> Iterator[Session]:
    """A sync session that is always rolled back.

    Embedding generation is offline batch work and runs on the sync RQ
    worker, so its tests need a sync session rather than the async one the
    request path uses.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    engine = create_engine(get_settings().sync_database_url)
    try:
        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            try:
                yield session
            finally:
                session.rollback()
    finally:
        engine.dispose()


@pytest.fixture
async def db_session(database_available: bool) -> AsyncIterator[AsyncSession]:
    """An async session that is always rolled back.

    Uses its own engine rather than the app's shared one so that pooled
    asyncpg connections are never handed between this event loop and the one
    TestClient runs the app in.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()
