from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app
from app.modules.semantic.dependencies import get_semantic_service


def test_request_validation_errors_are_typed_and_do_not_echo_inputs() -> None:
    # Request validation must be tested independently of database configuration.
    # FastAPI may resolve route dependencies before formatting an invalid body,
    # so CI without DATABASE_URL would otherwise return the dependency's safe 503.
    app.dependency_overrides[get_semantic_service] = lambda: object()
    try:
        response = TestClient(app).post(
            "/api/context/resolve",
            json={"company_id": "do-not-reflect-this-value"},
        )
    finally:
        app.dependency_overrides.pop(get_semantic_service, None)

    assert response.status_code == 422
    body = response.json()
    detail = body["detail"]
    UUID(detail["trace_id"])
    assert response.headers["x-trace-id"] == detail["trace_id"]
    assert detail["code"] == "request_validation_error"
    assert detail["terminal_state"] == "validation_error"
    assert detail["retryable"] is False
    assert "body.company_id" in detail["field_details"]
    assert "do-not-reflect-this-value" not in response.text
