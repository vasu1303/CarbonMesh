from fastapi import APIRouter

from app.api.routes.db import router as database_router
from app.api.routes.health import router as health_router

api_router = APIRouter()
api_router.include_router(database_router, prefix="/db", tags=["database"])
api_router.include_router(health_router, prefix="/health", tags=["health"])
