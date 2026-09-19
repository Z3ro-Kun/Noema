from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api.deps import get_db
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.db import engine
from app.schemas.health import HealthResponse
from app.services.health_service import check_health

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    # Without this, pooled asyncpg connections are only closed by garbage
    # collection, which logs "coroutine ... was never awaited" warnings.
    await engine.dispose()


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["health"], response_model=HealthResponse)
async def root_health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """Unversioned alias of /api/v1/health, convenient for load balancers."""
    return await check_health(db)
