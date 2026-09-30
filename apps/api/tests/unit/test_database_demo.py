from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.db import router
from app.dependencies.database import get_db_session


def test_database_demo_sanitizes_session_failure() -> None:
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/db")
    test_app.dependency_overrides[get_db_session] = FailingSession

    response = TestClient(test_app).get("/api/db/demo")

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "database_unavailable"
    assert detail["message"] == "Database connection is unavailable."
    assert detail["retryable"] is True
    assert "secret diagnostic" not in response.text


def test_database_demo_returns_database_timestamp() -> None:
    database_time = datetime(2026, 1, 1, tzinfo=UTC)
    fake_session = FakeSession(database_time)
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/db")
    test_app.dependency_overrides[get_db_session] = lambda: fake_session

    response = TestClient(test_app).get("/api/db/demo")

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

    async def execute(self, query) -> FakeResult:
        self.executed_sql = str(query)
        return FakeResult(self.database_time)


class FailingSession:
    async def execute(self, _query) -> None:
        raise RuntimeError("secret diagnostic that must not reach the response")
