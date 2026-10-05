from fastapi import APIRouter, Depends

from app.api.routes.agents import router as agents_router
from app.api.routes.approvals import router as approvals_router
from app.api.routes.assurance import router as assurance_router
from app.api.routes.audit import router as audit_router
from app.api.routes.auth import router as auth_router
from app.api.routes.context import router as context_router
from app.api.routes.data_quality import router as data_quality_router
from app.api.routes.db import router as database_router
from app.api.routes.demo import router as demo_router
from app.api.routes.dispatch import router as dispatch_router
from app.api.routes.factors import router as factors_router
from app.api.routes.health import router as health_router
from app.api.routes.imports import router as imports_router
from app.api.routes.integrations import router as integrations_router
from app.api.routes.ledger import router as ledger_router
from app.api.routes.measurement_lineage import router as measurement_lineage_router
from app.api.routes.measurements import router as measurements_router
from app.api.routes.procurement import router as procurement_router
from app.api.routes.semantic import router as semantic_router
from app.api.routes.sources import router as sources_router
from app.api.routes.workspace import router as workspace_router
from app.core.auth import authorize_request

api_router = APIRouter(dependencies=[Depends(authorize_request)])
api_router.include_router(auth_router)
api_router.include_router(database_router, prefix="/db", tags=["database"])
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(demo_router, prefix="/demo", tags=["demo"])
api_router.include_router(semantic_router, prefix="/semantic", tags=["semantic"])
api_router.include_router(context_router, prefix="/context", tags=["semantic"])
api_router.include_router(imports_router)
api_router.include_router(data_quality_router)
api_router.include_router(measurements_router)
api_router.include_router(measurement_lineage_router, tags=["ledger"])
api_router.include_router(sources_router)
api_router.include_router(factors_router)
api_router.include_router(assurance_router, tags=["assurance"])
api_router.include_router(procurement_router, tags=["procurement"])
api_router.include_router(dispatch_router, tags=["dispatch"])
api_router.include_router(agents_router)
api_router.include_router(approvals_router, tags=["approvals"])
api_router.include_router(audit_router, tags=["audit"])
api_router.include_router(integrations_router, tags=["integrations"])
api_router.include_router(ledger_router)
api_router.include_router(workspace_router)
