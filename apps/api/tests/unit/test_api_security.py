from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.routes import demo
from app.middleware.security import ApiSecurityMiddleware


def _bounded_app(max_body_bytes: int = 8) -> FastAPI:
    application = FastAPI()
    application.add_middleware(ApiSecurityMiddleware, max_body_bytes=max_body_bytes)

    @application.post("/body")
    async def read_body(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    return application


def test_security_middleware_rejects_oversized_body_before_endpoint() -> None:
    response = TestClient(_bounded_app()).post(
        "/body",
        content=b"123456789",
        headers={"X-Trace-ID": "trace-safe"},
    )

    assert response.status_code == 413
    assert response.headers["X-Trace-ID"] == "trace-safe"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.json()["detail"]["code"] == "request_too_large"


def test_security_middleware_caps_streamed_body_without_content_length() -> None:
    response = TestClient(_bounded_app()).post(
        "/body",
        content=(chunk for chunk in (b"1234", b"56789")),
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_too_large"


def test_security_middleware_hardens_normal_responses() -> None:
    response = TestClient(_bounded_app()).post("/body", content=b"12345678")

    assert response.status_code == 200
    assert response.json() == {"size": 8}
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_demo_reset_is_disabled_without_operator_token(monkeypatch) -> None:
    monkeypatch.setattr(
        demo,
        "get_settings",
        lambda: SimpleNamespace(demo_reset_token=None),
    )

    with pytest.raises(HTTPException) as caught:
        demo.authorize_demo_reset(reset_token=None, trace_id="trace-safe")

    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "demo_reset_disabled"


def test_demo_reset_uses_constant_time_operator_token_check(monkeypatch) -> None:
    expected = "a-secure-reset-token"
    monkeypatch.setattr(
        demo,
        "get_settings",
        lambda: SimpleNamespace(demo_reset_token=SecretStr(expected)),
    )

    with pytest.raises(HTTPException) as caught:
        demo.authorize_demo_reset(
            reset_token="a-different-token",
            trace_id="trace-safe",
        )
    assert caught.value.status_code == 403
    assert caught.value.detail["code"] == "demo_reset_forbidden"

    assert demo.authorize_demo_reset(reset_token=expected, trace_id="trace-safe") is None
