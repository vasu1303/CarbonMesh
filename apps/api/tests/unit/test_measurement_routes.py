from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.measurements import get_measurement_service, router
from app.modules.measurement.schemas import MeasurementListResponse
from app.modules.measurement.service import MeasurementServiceError


class FakeMeasurementService:
    def __init__(self) -> None:
        self.list_arguments: dict[str, object] | None = None
        self.database_error = False

    async def calculate(self, request):
        raise MeasurementServiceError(
            status_code=409,
            code="emission_factor_ambiguous",
            message="Multiple equally specific emission factors match an activity record.",
            terminal_state="needs_clarification",
            trace_id=request.trace_id or "measurement-route-trace",
            field_details={"factor_ids": [str(uuid4()), str(uuid4())]},
        )

    async def list_measurements(self, **kwargs) -> MeasurementListResponse:
        if self.database_error:
            raise SQLAlchemyError("secret database diagnostic")
        self.list_arguments = kwargs
        return MeasurementListResponse(
            items=[],
            total=0,
            limit=int(kwargs["limit"]),
            offset=int(kwargs["offset"]),
        )

    async def get_measurement(self, *, company_id: UUID, measurement_id: UUID):
        raise MeasurementServiceError(
            status_code=404,
            code="measurement_not_found",
            message="The requested measurement was not found for this company.",
            terminal_state="no_data",
            trace_id="measurement-detail-trace",
            field_details={"measurement_id": str(measurement_id)},
        )


def _client(service: FakeMeasurementService) -> TestClient:
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_measurement_service] = lambda: service
    return TestClient(application)


def test_calculate_route_returns_typed_ambiguous_factor_error() -> None:
    service = FakeMeasurementService()
    company_id = uuid4()
    response = _client(service).post(
        "/api/v1/measurements/calculate",
        json={
            "company_id": str(company_id),
            "site_id": str(uuid4()),
            "reporting_period_id": str(uuid4()),
            "material_code": "PACKAGING_TRAY",
            "trace_id": "measurement-route-trace",
        },
    )

    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "emission_factor_ambiguous"
    assert body["terminal_state"] == "needs_clarification"
    assert body["trace_id"] == "measurement-route-trace"
    assert body["retryable"] is False


def test_list_route_forwards_bounded_filters() -> None:
    service = FakeMeasurementService()
    company_id = uuid4()
    site_id = uuid4()
    response = _client(service).get(
        "/api/v1/measurements",
        params={
            "company_id": str(company_id),
            "site_id": str(site_id),
            "category": "emissions.scope3.category1",
            "status": "verified",
            "limit": 50,
            "offset": 10,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 10}
    assert service.list_arguments == {
        "company_id": company_id,
        "site_id": site_id,
        "reporting_period_id": None,
        "category": "emissions.scope3.category1",
        "status": "verified",
        "limit": 50,
        "offset": 10,
    }


def test_detail_route_is_tenant_scoped_and_returns_safe_not_found() -> None:
    service = FakeMeasurementService()
    measurement_id = uuid4()
    response = _client(service).get(
        f"/api/v1/measurements/{measurement_id}",
        params={"company_id": str(uuid4())},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "measurement_not_found",
        "message": "The requested measurement was not found for this company.",
        "trace_id": "measurement-detail-trace",
        "retryable": False,
        "terminal_state": "no_data",
        "field_details": {"measurement_id": str(measurement_id)},
    }


def test_database_failure_is_redacted_and_returns_503() -> None:
    service = FakeMeasurementService()
    service.database_error = True
    response = _client(service).get(
        "/api/v1/measurements",
        params={"company_id": str(uuid4())},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "measurement_service_unavailable"
    assert response.json()["detail"]["retryable"] is True
    assert "secret database diagnostic" not in response.text
