from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from app.db.models.core import Actor, Company, ReportingPeriod, Site
from app.db.models.procurement import Supplier
from app.db.models.semantic import MetricDefinition
from app.modules.semantic.schemas import ContextResolveRequest
from app.modules.semantic.service import (
    ContextResolutionError,
    SemanticService,
    build_analysis_signature,
)

COMPANY_ID = UUID("00000000-0000-0000-0000-000000000001")
SITE_ID = UUID("00000000-0000-0000-0000-000000000002")
PERIOD_ID = UUID("00000000-0000-0000-0000-000000000003")
METRIC_ONE_ID = UUID("00000000-0000-0000-0000-000000000004")
METRIC_TWO_ID = UUID("00000000-0000-0000-0000-000000000005")
SUPPLIER_ID = UUID("00000000-0000-0000-0000-000000000006")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000007")
OTHER_COMPANY_ID = UUID("00000000-0000-0000-0000-000000000099")


@pytest.mark.asyncio
async def test_lists_active_metrics_from_repository_in_public_contract() -> None:
    repository = FakeSemanticRepository()

    result = await SemanticService(repository).list_metrics(COMPANY_ID)

    assert result.company_id == COMPANY_ID
    assert result.count == 2
    assert [item.key for item in result.items] == [
        "procurement.cost_delta_pct",
        "emissions.scope3.category1",
    ]
    assert result.items[1].canonical_unit == "kgCO2e"
    assert repository.active_only is True


@pytest.mark.asyncio
async def test_context_resolution_freezes_resolved_records_and_stable_signature() -> None:
    repository = FakeSemanticRepository()
    service = SemanticService(repository)
    first_request = _request(
        metric_definition_ids=[METRIC_ONE_ID, METRIC_TWO_ID],
        material_scope=["packaging-tray", "resin"],
        max_cost_increase_pct="5.00",
    )
    reordered_request = _request(
        metric_definition_ids=[METRIC_TWO_ID, METRIC_ONE_ID],
        material_scope=["resin", "packaging-tray"],
        max_cost_increase_pct="5.0",
    )

    first = await service.resolve_context(first_request)
    reordered = await service.resolve_context(reordered_request)

    assert first.company.name == "Nova Components Ltd"
    assert first.company.is_synthetic is True
    assert first.site.name == "Plant B"
    assert first.reporting_period.name == "Q3 2026"
    assert [(item.key, item.version) for item in first.metrics] == [
        ("emissions.scope3.category1", "1.0"),
        ("procurement.cost_delta_pct", "1.0"),
    ]
    assert first.actor is not None
    assert first.actor.role == "sustainability_analyst"
    assert first.supplier_scope[0].name == "Circular Packaging Co"
    assert first.material_scope == ["packaging-tray", "resin"]
    assert first.constraints.max_cost_increase_pct == Decimal("5.00")
    assert first.analysis_signature == reordered.analysis_signature
    assert len(first.analysis_signature) == 64


@pytest.mark.asyncio
async def test_context_resolution_rejects_cross_company_site() -> None:
    repository = FakeSemanticRepository()
    repository.site.company_id = OTHER_COMPANY_ID

    with pytest.raises(ContextResolutionError) as caught:
        await SemanticService(repository).resolve_context(_request())

    assert caught.value.code == "context_relationship_mismatch"
    assert caught.value.fields == {"site_id": "company_mismatch"}


@pytest.mark.asyncio
async def test_context_resolution_rejects_missing_metric_without_partial_envelope() -> None:
    repository = FakeSemanticRepository()
    repository.metrics = [repository.metrics[0]]

    with pytest.raises(ContextResolutionError) as caught:
        await SemanticService(repository).resolve_context(
            _request(metric_definition_ids=[METRIC_ONE_ID, METRIC_TWO_ID])
        )

    assert caught.value.code == "metric_definition_not_found"
    assert caught.value.fields == {"metric_definition_ids": "not_found"}


def test_signature_canonicalizes_decimal_scale_and_mapping_order() -> None:
    first = {
        "constraint": Decimal("5.000"),
        "nested": {"b": 2, "a": "value"},
    }
    second = {
        "nested": {"a": "value", "b": 2},
        "constraint": Decimal("5.0"),
    }

    assert build_analysis_signature(first) == build_analysis_signature(second)
    assert build_analysis_signature(first) == (
        "99cbb1623d7429aee84de1f816390e70559673819d3c3e6ae44ef58c604fce4a"
    )


def _request(
    *,
    metric_definition_ids: list[UUID] | None = None,
    material_scope: list[str] | None = None,
    max_cost_increase_pct: str = "5",
) -> ContextResolveRequest:
    return ContextResolveRequest.model_validate(
        {
            "company_id": COMPANY_ID,
            "site_id": SITE_ID,
            "reporting_period_id": PERIOD_ID,
            "metric_definition_ids": metric_definition_ids or [METRIC_ONE_ID],
            "workflow": "measurement_procurement",
            "actor_id": ACTOR_ID,
            "supplier_scope": [SUPPLIER_ID],
            "material_scope": material_scope or ["packaging-tray"],
            "constraints": {
                "max_cost_increase_pct": max_cost_increase_pct,
                "max_lead_time_days": 14,
                "minimum_circularity_score": "60",
            },
        }
    )


class FakeSemanticRepository:
    def __init__(self) -> None:
        self.company = Company(
            id=COMPANY_ID,
            code="NOVA",
            name="Nova Components Ltd",
            is_synthetic=True,
            is_active=True,
        )
        self.site = Site(
            id=SITE_ID,
            company_id=COMPANY_ID,
            code="PLANT-B",
            name="Plant B",
            country_code="IN",
            timezone="Asia/Kolkata",
            is_active=True,
        )
        self.period = ReportingPeriod(
            id=PERIOD_ID,
            company_id=COMPANY_ID,
            name="Q3 2026",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 9, 30),
            status="closed",
        )
        self.metrics = [
            MetricDefinition(
                id=METRIC_ONE_ID,
                company_id=COMPANY_ID,
                key="emissions.scope3.category1",
                version="1.0",
                name="Scope 3 Category 1 emissions",
                canonical_unit="kgCO2e",
                dimensions={"site": True, "period": True},
                handler="measurement.calculate_scope3_category1",
                method_version="measurement-v1",
                description="Purchased goods and services emissions.",
                is_active=True,
            ),
            MetricDefinition(
                id=METRIC_TWO_ID,
                company_id=COMPANY_ID,
                key="procurement.cost_delta_pct",
                version="1.0",
                name="Procurement cost delta",
                canonical_unit="percent",
                dimensions={"scenario": True},
                handler="procurement.calculate_cost_delta",
                method_version="procurement-v1",
                description="Unit cost delta from the current product.",
                is_active=True,
            ),
        ]
        self.actor = Actor(
            id=ACTOR_ID,
            company_id=COMPANY_ID,
            email="analyst@example.test",
            display_name="Demo Analyst",
            role="sustainability_analyst",
            is_active=True,
        )
        self.supplier = Supplier(
            id=SUPPLIER_ID,
            company_id=COMPANY_ID,
            supplier_code="CIRCULAR-01",
            name="Circular Packaging Co",
            country_code="IN",
            status="active",
            supplier_metadata={},
        )
        self.active_only: bool | None = None

    async def list_metrics(
        self, company_id: UUID, *, active_only: bool = True
    ) -> list[MetricDefinition]:
        assert company_id == COMPANY_ID
        self.active_only = active_only
        # Return repository order to verify that list responses do not invent values.
        return [self.metrics[1], self.metrics[0]]

    async def get_company(self, company_id: UUID) -> Company | None:
        return self.company if company_id == self.company.id else None

    async def get_site(self, company_id: UUID, site_id: UUID) -> Site | None:
        return self.site if company_id == COMPANY_ID and site_id == self.site.id else None

    async def get_reporting_period(
        self, company_id: UUID, reporting_period_id: UUID
    ) -> ReportingPeriod | None:
        return (
            self.period
            if company_id == COMPANY_ID and reporting_period_id == self.period.id
            else None
        )

    async def get_actor(self, company_id: UUID, actor_id: UUID) -> Actor | None:
        return self.actor if company_id == COMPANY_ID and actor_id == self.actor.id else None

    async def get_metrics(
        self, company_id: UUID, metric_definition_ids: list[UUID]
    ) -> list[MetricDefinition]:
        if company_id != COMPANY_ID:
            return []
        requested = set(metric_definition_ids)
        return [metric for metric in reversed(self.metrics) if metric.id in requested]

    async def get_suppliers(
        self, company_id: UUID, supplier_ids: list[UUID]
    ) -> list[Supplier]:
        return (
            [self.supplier]
            if company_id == COMPANY_ID and self.supplier.id in supplier_ids
            else []
        )
