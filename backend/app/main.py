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

# Import-time, before a worker binds a port. A production deployment holding a
# development configuration should fail to start with a message naming what is
# wrong, rather than come up healthy and serve nobody.
settings.verify_production()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    # Without this, pooled asyncpg connections are only closed by garbage
    # collection, which logs "coroutine ... was never awaited" warnings.
    await engine.dispose()


def docs_urls(is_production: bool) -> dict[str, str | None]:
    """Where the interactive docs live, or that they do not live anywhere.

    `/docs`, `/redoc` and `/openapi.json` publish the whole API -- every path,
    every schema, every field name -- to anyone who can reach the origin, and
    that includes the inspection routes `require_internal_surface` answers 404
    for. A 404 contradicted by a machine-readable index is not much of a 404.

    So in production the schema is not served at all. Locally it stays on,
    because it is how the API is read while it is being built.

    A function rather than an expression so the rule can be asserted without
    reimporting this module under a different environment.
    """
    if is_production:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    lifespan=lifespan,
    **docs_urls(settings.is_production),
)

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
    """Unversioned alias of /api/v1/health, convenient for load balancers.

    Says whether the process is up and whether its two backing services
    answer. Nothing else: no configuration, no connection strings, no
    versions of anything but this application, and no error text -- a health
    endpoint is reachable by anyone who can reach the API.
    """
    return await check_health(db)
