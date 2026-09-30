from fastapi import APIRouter

from app.api.routes.agents import router as agents_router
from app.api.routes.approvals import router as approvals_router
from app.api.routes.audit import router as audit_router
from app.api.routes.db import router as database_router
from app.api.routes.health import router as health_router
from app.api.routes.integrations import router as integrations_router
from app.api.routes.measurement_lineage import router as measurement_lineage_router
from app.api.routes.procurement import router as procurement_router

api_router = APIRouter()
api_router.include_router(database_router, prefix="/db", tags=["database"])
api_router.include_router(health_router, prefix="/health", tags=["health"])

v1_router = APIRouter()
v1_router.include_router(health_router, prefix="/health", tags=["health"])
v1_router.include_router(measurement_lineage_router, tags=["ledger"])
v1_router.include_router(procurement_router, tags=["procurement"])
v1_router.include_router(agents_router)
v1_router.include_router(approvals_router, tags=["approvals"])
v1_router.include_router(audit_router, tags=["audit"])
v1_router.include_router(integrations_router, tags=["integrations"])
api_router.include_router(v1_router, prefix="/v1")
