from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import session_scope

router = APIRouter()


class DatabaseDemoResponse(BaseModel):
    connected: bool
    database_time: datetime
    message: str


@router.get("/demo", response_model=DatabaseDemoResponse)
def run_database_demo() -> DatabaseDemoResponse:
    """Verify the Neon connection and demonstrate a parameterized query."""
    try:
        with session_scope() as session:
            database_time = session.execute(text("SELECT CURRENT_TIMESTAMP")).scalar_one()
    except (RuntimeError, SQLAlchemyError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"Database connection failed: {error}",
        ) from error

    return DatabaseDemoResponse(
        connected=True,
        database_time=database_time,
        message="Connected to Neon PostgreSQL.",
    )
