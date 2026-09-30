from __future__ import annotations

from app.dependencies.database import DatabaseSession
from app.modules.semantic.repository import SemanticRepository
from app.modules.semantic.service import SemanticService


async def get_semantic_service(session: DatabaseSession) -> SemanticService:
    """Compose one request-scoped semantic service over an async DB session."""
    return SemanticService(SemanticRepository(session))
