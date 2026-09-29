from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import app


def test_database_demo_requires_database_url(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    response = TestClient(app).get("/api/db/demo")

    assert response.status_code == 503
    assert "DATABASE_URL is not configured" in response.json()["detail"]


def test_database_demo_returns_database_timestamp(monkeypatch) -> None:
    database_time = datetime(2026, 1, 1, tzinfo=UTC)
    fake_session = FakeSession(database_time)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
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

    @contextmanager
    def scope(self):
        yield self

    def execute(self, query) -> FakeResult:
        self.executed_sql = str(query)
        return FakeResult(self.database_time)
