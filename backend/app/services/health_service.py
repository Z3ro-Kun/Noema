from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.schemas.health import HealthResponse


async def check_health(db: AsyncSession) -> HealthResponse:
    try:
        await db.execute(text("SELECT 1"))
        database_status = "connected"
    except Exception:
        database_status = "unavailable"

    return HealthResponse(
        status="ok" if database_status == "connected" else "degraded",
        database=database_status,
        version=__version__,
    )
