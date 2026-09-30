from fastapi import APIRouter

from app.api.routes.agents import router as agents_router
from app.api.routes.approvals import router as approvals_router
from app.api.routes.audit import router as audit_router
from app.api.routes.context import router as context_router
from app.api.routes.data_quality import router as data_quality_router
from app.api.routes.db import router as database_router
from app.api.routes.demo import router as demo_router
from app.api.routes.health import router as health_router
from app.api.routes.imports import router as imports_router
from app.api.routes.integrations import router as integrations_router
from app.api.routes.measurement_lineage import router as measurement_lineage_router
from app.api.routes.measurements import router as measurements_router
from app.api.routes.procurement import router as procurement_router
from app.api.routes.semantic import router as semantic_router

api_router = APIRouter()
api_router.include_router(database_router, prefix="/db", tags=["database"])
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(demo_router, prefix="/v1/demo", tags=["demo"])
api_router.include_router(semantic_router, prefix="/v1/semantic", tags=["semantic"])
api_router.include_router(context_router, prefix="/v1/context", tags=["semantic"])
api_router.include_router(imports_router, prefix="/v1")
api_router.include_router(data_quality_router, prefix="/v1")
api_router.include_router(measurements_router, prefix="/v1")

v1_router = APIRouter()
v1_router.include_router(health_router, prefix="/health", tags=["health"])
v1_router.include_router(measurement_lineage_router, tags=["ledger"])
v1_router.include_router(procurement_router, tags=["procurement"])
v1_router.include_router(agents_router)
v1_router.include_router(approvals_router, tags=["approvals"])
v1_router.include_router(audit_router, tags=["audit"])
v1_router.include_router(integrations_router, tags=["integrations"])
api_router.include_router(v1_router, prefix="/v1")
