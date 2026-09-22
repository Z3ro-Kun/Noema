"""Is the application alive, and do its two backing services answer?

Minimal on purpose. Each check is one round trip with its own try/except, so
one unavailable service cannot mask the other and neither can take the
endpoint down. Exceptions are swallowed rather than reported: the caller
learns *that* a service did not answer, and the reason belongs in the logs.
"""

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.core.config import get_settings
from app.schemas.health import HealthResponse


async def check_health(db: AsyncSession) -> HealthResponse:
    database_status = "unavailable"
    try:
        await db.execute(text("SELECT 1"))
        database_status = "connected"
    except Exception:
        database_status = "unavailable"

    redis_status = "unavailable"
    try:
        # A short timeout: a health check that hangs is worse than one that
        # reports a problem, because a platform reads a hang as a dead process.
        client = Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        try:
            if client.ping():
                redis_status = "connected"
        finally:
            client.close()
    except (RedisError, OSError, ValueError):
        redis_status = "unavailable"

    healthy = database_status == "connected" and redis_status == "connected"
    return HealthResponse(
        status="ok" if healthy else "degraded",
        database=database_status,
        redis=redis_status,
        version=__version__,
    )
