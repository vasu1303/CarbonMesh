"""The application exposes a trusted demo workspace without credential gates."""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.modules.semantic.dependencies import get_semantic_service
from app.modules.semantic.schemas import MetricDefinitionList


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer obsolete-synthetic-token"},
    {"Cookie": "carbonmesh_session=obsolete-synthetic-session"},
])
def test_application_reads_are_credential_free_even_with_legacy_auth_settings(
    monkeypatch, headers,
):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTH_ACCESS_KEYS", "obsolete-invalid-configuration")
    monkeypatch.setenv("AUTH_SESSION_SECONDS", "obsolete-invalid-duration")
    get_settings()
    company_id = UUID("00000000-0000-4000-8000-000000000001")
    service = AsyncMock()
    service.list_metrics.return_value = MetricDefinitionList(
        company_id=company_id, items=[], count=0,
    )
    monkeypatch.setitem(app.dependency_overrides, get_semantic_service, lambda: service)

    response = TestClient(app).get(
        "/api/semantic/metrics", params={"company_id": str(company_id)}, headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"company_id": str(company_id), "items": [], "count": 0}
    assert "set-cookie" not in response.headers
    service.list_metrics.assert_awaited_once_with(company_id, active_only=True)


def test_openapi_does_not_advertise_authentication() -> None:
    schema = app.openapi()

    assert not schema.get("security")
    assert not schema.get("components", {}).get("securitySchemes")
    assert not any(path.startswith("/api/auth") for path in schema["paths"])
    for path in schema["paths"].values():
        for method, operation in path.items():
            if method in {"get", "post", "patch", "delete", "put", "head", "options"}:
                assert not operation.get("security")


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_session_endpoint_is_removed(method: str) -> None:
    response = TestClient(app).request(method, "/api/auth/session")

    assert response.status_code == 404
    assert "set-cookie" not in response.headers
