import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.dependencies.database import get_db_session
from app.main import app as application
from app.main import handle_database_error, handle_unexpected_error


def _error_test_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(SQLAlchemyError, handle_database_error)
    app.add_exception_handler(Exception, handle_unexpected_error)

    @app.get("/database-error")
    async def database_error() -> None:
        raise SQLAlchemyError("secret SQL and parameters")

    @app.get("/unexpected-error")
    async def unexpected_error() -> None:
        raise RuntimeError("secret implementation detail")

    return app


def test_database_errors_are_retryable_and_sanitized() -> None:
    with TestClient(_error_test_app(), raise_server_exceptions=False) as client:
        response = client.get("/database-error", headers={"X-Trace-ID": "trace-safe"})

    assert response.status_code == 503
    assert response.headers["X-Trace-ID"] == "trace-safe"
    assert response.json() == {
        "detail": {
            "code": "data_unavailable",
            "message": "Application data is temporarily unavailable.",
            "trace_id": "trace-safe",
            "retryable": True,
            "field_details": [],
        }
    }
    assert "secret" not in response.text


def test_unexpected_errors_replace_an_invalid_trace_id() -> None:
    with TestClient(_error_test_app(), raise_server_exceptions=False) as client:
        response = client.get("/unexpected-error", headers={"X-Trace-ID": "unsafe trace"})

    detail = response.json()["detail"]
    assert response.status_code == 500
    assert detail["code"] == "internal_error"
    assert detail["trace_id"] != "unsafe trace"
    assert response.headers["X-Trace-ID"] == detail["trace_id"]
    assert "secret" not in response.text


@pytest.mark.parametrize("error", [
    socket.gaierror(11001, "secret database host"),
    ConnectionRefusedError("secret database host"),
    ConnectionResetError("secret database host"),
    TimeoutError("secret connection details"),
])
def test_raw_connection_failures_use_the_real_apps_safe_error_handler(error) -> None:
    async def unavailable():
        raise error

    application.dependency_overrides[get_db_session] = unavailable
    try:
        response = TestClient(application).get(
            "/api/semantic/metrics",
            params={"company_id": "00000000-0000-4000-8000-000000000001"},
            headers={"X-Trace-ID": "connection-test"},
        )
    finally:
        application.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "data_unavailable"
    assert response.json()["detail"]["retryable"] is True
    assert response.headers["x-trace-id"] == "connection-test"
    assert "secret" not in response.text


def test_unrelated_os_errors_are_not_misreported_as_database_failures() -> None:
    async def unavailable():
        raise FileNotFoundError("secret source path")

    application.dependency_overrides[get_db_session] = unavailable
    try:
        response = TestClient(application, raise_server_exceptions=False).get(
            "/api/semantic/metrics",
            params={"company_id": "00000000-0000-4000-8000-000000000001"},
        )
    finally:
        application.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "internal_error"
    assert "secret" not in response.text
