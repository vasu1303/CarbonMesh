from __future__ import annotations

import pytest

from app.dependencies import database as database_dependencies


def test_database_session_factory_dependency_is_overridable(monkeypatch) -> None:
    expected_factory = object()
    monkeypatch.setattr(
        database_dependencies,
        "get_session_factory",
        lambda: expected_factory,
    )

    assert database_dependencies.get_db_session_factory() is expected_factory


@pytest.mark.asyncio
async def test_database_session_dependency_uses_and_closes_injected_factory() -> None:
    session = FakeDependencySession()
    dependency = database_dependencies.get_db_session(lambda: session)  # type: ignore[arg-type]

    assert await anext(dependency) is session
    with pytest.raises(StopAsyncIteration):
        await anext(dependency)

    assert session.entered
    assert session.exited
    assert not session.rolled_back


class FakeDependencySession:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False
        self.rolled_back = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *_):
        self.exited = True

    async def rollback(self) -> None:
        self.rolled_back = True
