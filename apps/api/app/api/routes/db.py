from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import session_scope

router = APIRouter()


class DatabaseDemoResponse(BaseModel):
    connected: bool
    database_time: datetime
    message: str


@router.get("/demo", response_model=DatabaseDemoResponse)
async def run_database_demo() -> DatabaseDemoResponse:
    """Verify the Neon connection and demonstrate a parameterized query."""
    try:
        async with session_scope() as session:
            result = await session.execute(text("SELECT CURRENT_TIMESTAMP"))
            database_time = result.scalar_one()
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="Database connection is unavailable.",
        ) from error

    return DatabaseDemoResponse(
        connected=True,
        database_time=database_time,
        message="Connected to Neon PostgreSQL.",
    )
