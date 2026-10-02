import asyncio
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import safe_http_error
from app.db.bootstrap import (
    DatabaseBootstrapError,
    load_model_registry,
    verify_database_contract,
)
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


@router.get("", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    return HealthResponse(status="ok", service="CarbonMesh API")


@router.get("/ready", response_model=HealthResponse)
async def get_readiness(
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> HealthResponse:
    """Read-only verification of the database contract; never bootstrap on startup."""
    try:
        async with asyncio.timeout(10):
            connection = await session.connection()
            await verify_database_contract(connection, load_model_registry())
    except (SQLAlchemyError, DatabaseBootstrapError, OSError) as error:
        raise safe_http_error(
            status_code=503,
            code="database_not_ready",
            message="The database is unavailable or its schema contract is incompatible.",
            trace_id=trace_id,
            retryable=True,
        ) from error
    return HealthResponse(status="ok", service="CarbonMesh API")
