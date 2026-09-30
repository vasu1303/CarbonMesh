from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import get_session_factory

type DatabaseSessionFactory = async_sessionmaker[AsyncSession]


def get_db_session_factory() -> DatabaseSessionFactory:
    """Return the process session factory through an overridable dependency boundary."""

    return get_session_factory()


async def get_db_session(
    session_factory: Annotated[DatabaseSessionFactory, Depends(get_db_session_factory)],
) -> AsyncIterator[AsyncSession]:
    """Yield one request-scoped session without committing implicitly.

    Application services own their transaction boundary. Keeping session creation
    behind the shared factory dependency keeps request, worker, and stream sessions
    on the same overridable database boundary.
    """

    async with session_factory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise


type DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
type DatabaseSessionFactoryDependency = Annotated[
    DatabaseSessionFactory,
    Depends(get_db_session_factory),
]
