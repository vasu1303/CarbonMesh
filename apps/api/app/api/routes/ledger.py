from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
from app.modules.ledger.schemas import LedgerEventDetail, LedgerEventListResult
from app.modules.ledger.service import (
    InvalidLedgerQueryError,
    LedgerEventNotFoundError,
    get_ledger_event_detail,
    search_ledger_events,
)

router = APIRouter(prefix="/ledger", tags=["ledger"])


@router.get(
    "/events",
    response_model=LedgerEventListResult,
    summary="Search immutable ledger events",
)
async def list_ledger_events(
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    session: DatabaseSession,
    event_type: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    entity_type: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    entity_id: UUID | None = None,
    agent_run_id: UUID | None = None,
    created_from: Annotated[
        datetime | None,
        Query(description="Inclusive timestamp lower bound; a UTC offset is required."),
    ] = None,
    created_to: Annotated[
        datetime | None,
        Query(description="Inclusive timestamp upper bound; a UTC offset is required."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    trace_id: TraceIdHeader = None,
) -> LedgerEventListResult:
    try:
        return await search_ledger_events(
            session,
            company_id=company_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            agent_run_id=agent_run_id,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
            offset=offset,
        )
    except InvalidLedgerQueryError as error:
        raise safe_http_error(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="invalid_ledger_query",
            message=str(error),
            trace_id=trace_id,
            retryable=False,
            field_details={error.field: str(error)},
        ) from error
    except SQLAlchemyError as error:
        raise safe_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="ledger_unavailable",
            message="Ledger events are temporarily unavailable.",
            trace_id=trace_id,
            retryable=True,
        ) from error


@router.get(
    "/events/{event_id}",
    response_model=LedgerEventDetail,
    summary="Read a ledger event with evidence and immediate lineage",
)
async def read_ledger_event(
    event_id: UUID,
    company_id: Annotated[UUID, Query(description="Company tenant scope")],
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> LedgerEventDetail:
    try:
        return await get_ledger_event_detail(
            session,
            company_id=company_id,
            event_id=event_id,
        )
    except LedgerEventNotFoundError as error:
        raise safe_http_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="ledger_event_not_found",
            message="The requested ledger event was not found.",
            trace_id=trace_id,
            retryable=False,
            field_details={"event_id": str(event_id)},
        ) from error
    except SQLAlchemyError as error:
        raise safe_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="ledger_unavailable",
            message="The ledger event is temporarily unavailable.",
            trace_id=trace_id,
            retryable=True,
        ) from error
