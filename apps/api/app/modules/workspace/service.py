from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.workspace import repository
from app.modules.workspace.schemas import (
    WorkspaceOption,
    WorkspaceOptionsQuery,
    WorkspaceOptionsResult,
)


async def list_workspace_options(
    session: AsyncSession,
    query: WorkspaceOptionsQuery,
) -> WorkspaceOptionsResult:
    rows, total = await repository.list_options(session, query)
    return WorkspaceOptionsResult(
        items=[WorkspaceOption.model_validate(row) for row in rows],
        total=total,
        limit=query.limit,
        offset=0 if query.id is not None else query.offset,
    )
