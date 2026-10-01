from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app


def test_request_validation_errors_are_typed_and_do_not_echo_inputs() -> None:
    response = TestClient(app).post(
        "/api/context/resolve",
        json={"company_id": "do-not-reflect-this-value"},
    )

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
