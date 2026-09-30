from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import DatabaseConfigurationError, session_scope
from app.modules.semantic.repository import SemanticRepository
from app.modules.semantic.service import SemanticService


async def get_semantic_service() -> AsyncIterator[SemanticService]:
    """Compose one request-scoped semantic service over an async DB session."""
    try:
        async with session_scope() as session:
            yield SemanticService(SemanticRepository(session))
    except (DatabaseConfigurationError, SQLAlchemyError, OSError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "database_unavailable",
                "message": "Semantic context is temporarily unavailable.",
                "trace_id": str(uuid4()),
                "retryable": True,
                "field_details": {},
            },
        ) from error
