from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.context import router as context_router
from app.api.routes.health import router as health_router
from app.api.routes.semantic import router as semantic_router
from app.modules.semantic.dependencies import get_semantic_service
from app.modules.semantic.schemas import (
    ContextConstraints,
    ContextEnvelope,
    MetricDefinitionList,
    MetricDefinitionRead,
    ResolvedCompany,
    ResolvedReportingPeriod,
    ResolvedSite,
)
from app.modules.semantic.service import ContextResolutionError

COMPANY_ID = UUID("00000000-0000-0000-0000-000000000001")
SITE_ID = UUID("00000000-0000-0000-0000-000000000002")
PERIOD_ID = UUID("00000000-0000-0000-0000-000000000003")
METRIC_ID = UUID("00000000-0000-0000-0000-000000000004")
SIGNATURE = "a" * 64


def test_health_router_supports_versioned_and_legacy_mounts() -> None:
    app = _app(FakeSemanticService())
    client = TestClient(app)

    assert client.get("/api/v1/health").json() == {
        "status": "ok",
        "service": "CarbonMesh API",
    }
    assert client.get("/api/health").json() == {
        "status": "ok",
        "service": "CarbonMesh API",
    }


def test_metric_route_returns_db_service_contract() -> None:
    app = _app(FakeSemanticService())

    response = TestClient(app).get(
        "/api/v1/semantic/metrics",
        params={"company_id": str(COMPANY_ID)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "company_id": str(COMPANY_ID),
        "items": [
            {
                "id": str(METRIC_ID),
                "key": "emissions.scope3.category1",
                "version": "1.0",
                "name": "Scope 3 Category 1 emissions",
                "canonical_unit": "kgCO2e",
                "dimensions": {"site": True, "period": True},
                "handler": "measurement.calculate_scope3_category1",
                "method_version": "measurement-v1",
                "description": None,
            }
        ],
        "count": 1,
    }


def test_context_route_returns_frozen_resolved_context() -> None:
    app = _app(FakeSemanticService())

    response = TestClient(app).post(
        "/api/v1/context/resolve",
        json={
            "company_id": str(COMPANY_ID),
            "site_id": str(SITE_ID),
            "reporting_period_id": str(PERIOD_ID),
            "metric_definition_ids": [str(METRIC_ID)],
            "workflow": "measurement",
            "constraints": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["company"]["name"] == "Nova Components Ltd"
    assert response.json()["site"]["name"] == "Plant B"
    assert response.json()["metrics"][0]["canonical_unit"] == "kgCO2e"
    assert response.json()["analysis_signature"] == SIGNATURE


def test_context_route_exposes_typed_safe_error() -> None:
    app = _app(FailingSemanticService())

    response = TestClient(app).post(
        "/api/v1/context/resolve",
        json={
            "company_id": str(COMPANY_ID),
            "site_id": str(SITE_ID),
            "reporting_period_id": str(PERIOD_ID),
            "metric_definition_ids": [str(METRIC_ID)],
            "workflow": "measurement",
        },
    )

    assert response.status_code == 422
    body = response.json()
    UUID(body["detail"].pop("trace_id"))
    assert body == {
        "detail": {
            "code": "context_relationship_mismatch",
            "message": "The requested site does not belong to the company.",
            "retryable": False,
            "field_details": {"site_id": "company_mismatch"},
        }
    }


def _app(service) -> FastAPI:
    app = FastAPI()
    app.include_router(health_router, prefix="/api/health")
    app.include_router(health_router, prefix="/api/v1/health")
    app.include_router(semantic_router, prefix="/api/v1/semantic")
    app.include_router(context_router, prefix="/api/v1/context")
    app.dependency_overrides[get_semantic_service] = lambda: service
    return app


def _metric() -> MetricDefinitionRead:
    return MetricDefinitionRead(
        id=METRIC_ID,
        key="emissions.scope3.category1",
        version="1.0",
        name="Scope 3 Category 1 emissions",
        canonical_unit="kgCO2e",
        dimensions={"site": True, "period": True},
        handler="measurement.calculate_scope3_category1",
        method_version="measurement-v1",
    )


class FakeSemanticService:
    async def list_metrics(
        self, company_id: UUID, *, active_only: bool = True
    ) -> MetricDefinitionList:
        assert company_id == COMPANY_ID
        assert active_only is True
        return MetricDefinitionList(company_id=company_id, items=[_metric()], count=1)

    async def resolve_context(self, request) -> ContextEnvelope:
        return ContextEnvelope(
            company=ResolvedCompany(
                id=request.company_id,
                code="NOVA",
                name="Nova Components Ltd",
                is_synthetic=True,
            ),
            site=ResolvedSite(
                id=request.site_id,
                code="PLANT-B",
                name="Plant B",
                country_code="IN",
                timezone="Asia/Kolkata",
            ),
            reporting_period=ResolvedReportingPeriod(
                id=request.reporting_period_id,
                name="Q3 2026",
                start_date=date(2026, 7, 1),
                end_date=date(2026, 9, 30),
                status="closed",
            ),
            metrics=[_metric()],
            workflow=request.workflow,
            actor=None,
            supplier_scope=[],
            material_scope=[],
            constraints=ContextConstraints(),
            analysis_signature=SIGNATURE,
        )


class FailingSemanticService(FakeSemanticService):
    async def resolve_context(self, request) -> ContextEnvelope:
        raise ContextResolutionError(
            "context_relationship_mismatch",
            "The requested site does not belong to the company.",
            fields={"site_id": "company_mismatch"},
        )
