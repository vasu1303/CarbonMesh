from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import health
from app.db.bootstrap import DatabaseBootstrapError
from app.dependencies.database import get_db_session


@pytest.mark.parametrize("error", [None, DatabaseBootstrapError("internal schema"),
                                  SQLAlchemyError("secret database URL"), TimeoutError()])
def test_readiness_checks_complete_contract_without_mutating_database(monkeypatch, error):
    session = AsyncMock()
    verifier = AsyncMock(side_effect=error)
    monkeypatch.setattr(health, "verify_database_contract", verifier)
    app = FastAPI()
    app.include_router(health.router, prefix="/api/health")
    app.dependency_overrides[get_db_session] = lambda: session
    response = TestClient(app).get("/api/health/ready", headers={"X-Trace-ID": "ready-test"})
    assert response.status_code == (200 if error is None else 503)
    verifier.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.execute.assert_not_awaited()
    if error is not None:
        assert response.json()["detail"]["code"] == "database_not_ready"
        assert response.json()["detail"]["trace_id"] == "ready-test"
        assert "secret" not in response.text
        assert "internal schema" not in response.text


def test_process_health_never_acquires_database_session():
    app = FastAPI()
    app.include_router(health.router, prefix="/api/health")

    def unavailable():
        raise AssertionError("Process health must not require a database")

    app.dependency_overrides[get_db_session] = unavailable
    assert TestClient(app).get("/api/health").status_code == 200


def test_readiness_sanitizes_raw_connection_failure():
    session = AsyncMock()
    session.connection.side_effect = ConnectionRefusedError("secret database host")
    app = FastAPI()
    app.include_router(health.router, prefix="/api/health")
    app.dependency_overrides[get_db_session] = lambda: session
    response = TestClient(app).get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database_not_ready"
    assert "secret" not in response.text
