from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.health import HealthResponse
from app.services.health_service import check_health

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    return await check_health(db)
