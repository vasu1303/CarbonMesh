from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader

router = APIRouter()


class DatabaseDemoResponse(BaseModel):
    connected: bool
    database_time: datetime
    message: str


@router.get("/demo", response_model=DatabaseDemoResponse)
async def run_database_demo(
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> DatabaseDemoResponse:
    """Verify the Neon connection and demonstrate a parameterized query."""
    try:
        result = await session.execute(text("SELECT CURRENT_TIMESTAMP"))
        database_time = result.scalar_one()
    except Exception as error:
        raise safe_http_error(
            status_code=503,
            code="database_unavailable",
            message="Database connection is unavailable.",
            trace_id=trace_id,
            retryable=True,
        ) from error

    return DatabaseDemoResponse(
        connected=True,
        database_time=database_time,
        message="Connected to Neon PostgreSQL.",
    )
