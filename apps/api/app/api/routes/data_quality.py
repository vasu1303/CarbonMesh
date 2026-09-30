from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import DatabaseConfigurationError, session_scope
from app.modules.imports.schemas import DataQualityIssueList
from app.modules.imports.service import ImportReferenceNotFound, ImportService

router = APIRouter(prefix="/data-quality", tags=["data-quality"])


@router.get("/issues", response_model=DataQualityIssueList)
async def list_data_quality_issues(
    company_id: Annotated[UUID, Query(description="Tenant owning the issues.")],
    issue_status: Annotated[
        Literal["open", "resolved", "waived"] | None, Query(alias="status")
    ] = "open",
    severity: Literal["info", "warning", "error"] | None = None,
    code: Annotated[str | None, Query(max_length=100)] = None,
    issue_type: Annotated[str | None, Query(max_length=100)] = None,
    import_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DataQualityIssueList:
    """List unresolved issues by default, with tenant-safe deterministic filters."""
    try:
        async with session_scope() as session:
            return await ImportService(session).list_data_quality_issues(
                company_id=company_id,
                status=issue_status,
                severity=severity,
                code=code,
                issue_type=issue_type,
                import_id=import_id,
                limit=limit,
                offset=offset,
            )
    except ImportReferenceNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": error.code,
                "message": str(error),
                "trace_id": uuid4().hex,
                "retryable": False,
                "field_details": (
                    {error.field_name: str(error)} if error.field_name is not None else {}
                ),
            },
        ) from error
    except (DatabaseConfigurationError, SQLAlchemyError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "database_unavailable",
                "message": "Data-quality issues are temporarily unavailable.",
                "trace_id": uuid4().hex,
                "retryable": True,
                "field_details": {},
            },
        ) from error
