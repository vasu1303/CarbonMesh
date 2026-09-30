from fastapi import APIRouter

from app.api.routes.context import router as context_router
from app.api.routes.data_quality import router as data_quality_router
from app.api.routes.db import router as database_router
from app.api.routes.demo import router as demo_router
from app.api.routes.health import router as health_router
from app.api.routes.imports import router as imports_router
from app.api.routes.measurements import router as measurements_router
from app.api.routes.semantic import router as semantic_router

api_router = APIRouter()
api_router.include_router(database_router, prefix="/db", tags=["database"])
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(health_router, prefix="/v1/health", tags=["health"])
api_router.include_router(demo_router, prefix="/v1/demo", tags=["demo"])
api_router.include_router(semantic_router, prefix="/v1/semantic", tags=["semantic"])
api_router.include_router(context_router, prefix="/v1/context", tags=["semantic"])
api_router.include_router(imports_router, prefix="/v1")
api_router.include_router(data_quality_router, prefix="/v1")
api_router.include_router(measurements_router, prefix="/v1")
