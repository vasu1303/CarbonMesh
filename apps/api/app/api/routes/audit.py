from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
from app.modules.audit.schemas import AuditEntityResult
from app.modules.audit.service import (
    AuditEntityNotFoundError,
    UnsupportedAuditEntityError,
    get_entity_audit,
)

router = APIRouter()


@router.get("/audit/{entity_type}/{entity_id}", response_model=AuditEntityResult)
async def read_entity_audit(
    entity_type: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,99}$")],
    entity_id: UUID,
    company_id: UUID,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> AuditEntityResult:
    try:
        return await get_entity_audit(
            session,
            company_id=company_id,
            entity_type=entity_type,
            entity_id=entity_id,
        )
    except UnsupportedAuditEntityError as error:
        raise safe_http_error(
            status_code=422,
            code="unsupported_entity_type",
            message="This entity type is not available in the audit explorer.",
            retryable=False,
            trace_id=trace_id,
            field_details={"entity_type": entity_type},
        ) from error
    except AuditEntityNotFoundError as error:
        raise safe_http_error(
            status_code=404,
            code="audit_entity_not_found",
            message="The requested audit entity was not found.",
            retryable=False,
            trace_id=trace_id,
            field_details={"entity_id": str(entity_id)},
        ) from error
