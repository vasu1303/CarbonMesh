from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import app


def test_database_demo_requires_database_url(monkeypatch) -> None:
    @asynccontextmanager
    async def unavailable_session() -> AsyncIterator[None]:
        raise RuntimeError("secret diagnostic that must not reach the response")
        yield  # pragma: no cover

    monkeypatch.setattr("app.api.routes.db.session_scope", unavailable_session)

    response = TestClient(app).get("/api/db/demo")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database connection is unavailable."}
    assert "secret diagnostic" not in response.text


def test_database_demo_returns_database_timestamp(monkeypatch) -> None:
    database_time = datetime(2026, 1, 1, tzinfo=UTC)
    fake_session = FakeSession(database_time)
    monkeypatch.setattr(
        "app.api.routes.db.session_scope",
        lambda: fake_session.scope(),
    )

    response = TestClient(app).get("/api/db/demo")

    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "database_time": "2026-01-01T00:00:00Z",
        "message": "Connected to Neon PostgreSQL.",
    }
    assert fake_session.executed_sql == "SELECT CURRENT_TIMESTAMP"


class FakeResult:
    def __init__(self, database_time: datetime) -> None:
        self.database_time = database_time

    def scalar_one(self) -> datetime:
        return self.database_time


class FakeSession:
    def __init__(self, database_time: datetime) -> None:
        self.database_time = database_time
        self.executed_sql = ""

    @asynccontextmanager
    async def scope(self) -> AsyncIterator[FakeSession]:
        yield self

    async def execute(self, query) -> FakeResult:
        self.executed_sql = str(query)
        return FakeResult(self.database_time)
