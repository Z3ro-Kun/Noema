from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    catalog,
    health,
    library,
    preference,
    recommendations,
    search,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(catalog.router, tags=["catalog"])
api_router.include_router(search.router, tags=["search"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(library.router, tags=["library"])
api_router.include_router(preference.router, tags=["preferences"])
api_router.include_router(recommendations.router, tags=["recommendations"])
