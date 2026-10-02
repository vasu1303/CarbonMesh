from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.agents.graph_contracts import (
    ContextConstraint,
    ContextEntity,
    ContextEnvelope,
)
from app.modules.agents.tool_ports import CarbonMeshAgentToolServicePort
from app.modules.agents.tools import AgentToolRegistry


def _context(
    *,
    company_id: UUID,
    site_id: UUID,
    period_id: UUID,
    actor_id: UUID,
    modules: tuple[str, ...],
    entities: tuple[ContextEntity, ...] = (),
    method_ids: tuple[UUID, ...] = (),
    material_scope: tuple[str, ...] = (),
    constraints: tuple[ContextConstraint, ...] = (),
) -> ContextEnvelope:
    return ContextEnvelope(
        company_id=company_id,
        site_id=site_id,
        reporting_period_id=period_id,
        actor_id=actor_id,
        actor_role="sustainability_analyst",
        modules=modules,
        metric_keys=("emissions.scope3.category1",),
        material_scope=material_scope,
        entities=entities,
        method_definition_ids=method_ids,
        constraints=constraints,
        request_hash="1" * 64,
        analysis_signature="2" * 64,
    )


def _measurement(
    *,
    company_id: UUID,
    site_id: UUID,
    period_id: UUID,
    measurement_id: UUID,
    activity_id: UUID,
    method_id: UUID,
    factor_id: UUID,
) -> SimpleNamespace:
    del company_id
    return SimpleNamespace(
        id=measurement_id,
        site_id=site_id,
        reporting_period_id=period_id,
        metric_key="emissions.scope3.category1",
        status="verified",
        inputs=[SimpleNamespace(activity_record_id=activity_id)],
        calculations=[SimpleNamespace(emission_factor_id=factor_id, activity_record_id=activity_id,
                                      grid_intensity_point_id=None, output_hash="a" * 64)],
        calculation_run=SimpleNamespace(id=uuid4(), method_definition_id=method_id),
        facts=SimpleNamespace(fact_id=uuid4(), ledger_event_id=uuid4()),
        value_kgco2e=Decimal("125.5000"),
        unit="kgCO2e",
        confidence=Decimal("0.9100"),
        output_hash="a" * 64,
    )


class _MeasurementService:
    def __init__(self, company_id: UUID, measurement: SimpleNamespace) -> None:
        self.company_id = company_id
        self.measurement = measurement
        self.calls: list[UUID] = []

    async def get_measurement(
        self,
        *,
        company_id: UUID,
        measurement_id: UUID,
        trace_id: str | None = None,
    ) -> SimpleNamespace:
        del trace_id
        assert company_id == self.company_id
        assert measurement_id == self.measurement.id
        self.calls.append(measurement_id)
        return self.measurement


class _WorkflowResolver:
    def __init__(self, resolved: SimpleNamespace) -> None:
        self.resolved = resolved
        self.requests: list[tuple[object, str]] = []

    async def resolve(self, *, context: object, workflow: str) -> SimpleNamespace:
        self.requests.append((context, workflow))
        return self.resolved


class _ProcurementService:
    def __init__(
        self,
        *,
        company_id: UUID,
        scenario: SimpleNamespace | None = None,
        recommendation: SimpleNamespace | None = None,
        products: list[SimpleNamespace] | None = None,
        preview_current: bool = True,
    ) -> None:
        self.company_id = company_id
        self.scenario = scenario
        self.recommendation = recommendation
        self.products = products or []
        self.preview_current = preview_current
        self.preview_checks: list[UUID] = []

    async def get_scenario(
        self,
        *,
        company_id: UUID,
        scenario_id: UUID,
    ) -> SimpleNamespace:
        assert company_id == self.company_id
        assert self.scenario is not None
        assert scenario_id == self.scenario.id
        return self.scenario

    async def get_recommendation(
        self,
        *,
        company_id: UUID,
        recommendation_id: UUID,
    ) -> SimpleNamespace:
        assert company_id == self.company_id
        assert self.recommendation is not None
        assert recommendation_id == self.recommendation.id
        return self.recommendation

    async def list_supplier_products(self, **kwargs: object) -> SimpleNamespace:
        assert kwargs["company_id"] == self.company_id
        return SimpleNamespace(items=self.products)

    async def is_recommendation_preview_current(
        self,
        *,
        company_id: UUID,
        recommendation_id: UUID,
    ) -> bool:
        assert company_id == self.company_id
        assert self.recommendation is not None
        assert recommendation_id == self.recommendation.id
        self.preview_checks.append(recommendation_id)
        return self.preview_current


class _DispatchService:
    def __init__(self, company_id: UUID, scenario: SimpleNamespace) -> None:
        self.company_id = company_id
        self.scenario = scenario
        self.calls: list[UUID] = []

    async def get_scenario(
        self,
        *,
        company_id: UUID,
        scenario_id: UUID,
    ) -> SimpleNamespace:
        assert company_id == self.company_id
        assert scenario_id == self.scenario.id
        self.calls.append(scenario_id)
        return self.scenario


@pytest.mark.asyncio
async def test_resolve_context_uses_existing_resolver_without_calculating() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    actor_id = uuid4()
    method_id = uuid4()
    activity_id = uuid4()
    measurement = SimpleNamespace(id=uuid4(), calculation_run_id=uuid4())
    resolved = SimpleNamespace(
        measurement=measurement,
        activity=SimpleNamespace(id=activity_id),
        current_product=SimpleNamespace(id=uuid4()),
        method=SimpleNamespace(id=method_id),
        rows_resolved=5,
    )
    resolver = _WorkflowResolver(resolved)
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(workflow_resolver=resolver)  # type: ignore[arg-type]
    )
    context = _context(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        actor_id=actor_id,
        modules=("measurement",),
        material_scope=("recycled aluminium",),
    )

    result = await registry.invoke(
        "resolve_context",
        context=context,
        payload={"include_metric_definitions": True},
    )

    assert result.status == "success"
    assert result.data["carbon_measurement_id"] == str(measurement.id)
    assert result.data["activity_record_ids"] == [str(activity_id)]
    assert result.data["method_definition_id"] == str(method_id)
    assert resolver.requests[0][1] == "measurement"


@pytest.mark.asyncio
async def test_resolve_context_enriches_co_present_procurement_and_dispatch() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    actor_id = uuid4()
    measurement_id = uuid4()
    activity_id = uuid4()
    measurement_method_id = uuid4()
    factor_id = uuid4()
    procurement_scenario_id = uuid4()
    dispatch_scenario_id = uuid4()
    dispatch_method_id = uuid4()
    flexible_load_id = uuid4()
    forecast_id = uuid4()
    measurement = _measurement(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        measurement_id=measurement_id,
        activity_id=activity_id,
        method_id=measurement_method_id,
        factor_id=factor_id,
    )
    procurement_scenario = SimpleNamespace(
        id=procurement_scenario_id,
        site_id=site_id,
        reporting_period_id=period_id,
        carbon_measurement_id=measurement_id,
        current_product=SimpleNamespace(id=uuid4(), material_code="AL-RECYCLED"),
        method=SimpleNamespace(id=uuid4()),
        constraints=SimpleNamespace(
            max_cost_increase_pct=Decimal(5),
            max_lead_time_days=30,
            minimum_circularity_score=Decimal(80),
            material=SimpleNamespace(allowed_material_codes=[]),
        ),
        alternatives=[],
        selected_recommendation=None,
    )
    dispatch_scenario = SimpleNamespace(
        id=dispatch_scenario_id,
        site_id=site_id,
        flexible_load=SimpleNamespace(
            id=flexible_load_id,
            duration_minutes=120,
            maximum_power_kw=Decimal(500),
        ),
        method=SimpleNamespace(id=dispatch_method_id),
        policy_definition_id=None,
        forecast_source_document_id=forecast_id,
        window_start=datetime(2026, 7, 1, tzinfo=UTC),
        window_end=datetime(2026, 7, 1, 12, tzinfo=UTC),
        constraints=SimpleNamespace(
            maximum_delay_minutes=240,
            source_constraints=[],
        ),
    )
    measurement_service = _MeasurementService(company_id, measurement)
    procurement_service = _ProcurementService(
        company_id=company_id,
        scenario=procurement_scenario,
    )
    dispatch_service = _DispatchService(company_id, dispatch_scenario)
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(
            measurement_service=measurement_service,  # type: ignore[arg-type]
            procurement_service=procurement_service,  # type: ignore[arg-type]
            dispatch_service=dispatch_service,  # type: ignore[arg-type]
        )
    )
    context = _context(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        actor_id=actor_id,
        modules=("procurement", "dispatch"),
        entities=(
            ContextEntity(
                entity_type="carbon_measurement",
                entity_id=measurement_id,
            ),
            ContextEntity(
                entity_type="procurement_scenario",
                entity_id=procurement_scenario_id,
            ),
            ContextEntity(
                entity_type="dispatch_scenario",
                entity_id=dispatch_scenario_id,
            ),
            ContextEntity(entity_type="flexible_load", entity_id=flexible_load_id),
        ),
    )

    result = await registry.invoke(
        "resolve_context",
        context=context,
        payload={"include_metric_definitions": True},
    )

    assert result.status == "success"
    assert result.data["scenario_id"] == str(procurement_scenario_id)
    assert result.data["procurement_scenario_id"] == str(procurement_scenario_id)
    assert result.data["dispatch_scenario_id"] == str(dispatch_scenario_id)
    assert result.data["forecast_id"] == str(forecast_id)
    assert result.data["forecast_source_document_id"] == str(forecast_id)
    assert result.data["dispatch_method_definition_id"] == str(dispatch_method_id)
    assert result.data["flexible_load_id"] == str(flexible_load_id)
    assert measurement_service.calls == [measurement_id]
    assert dispatch_service.calls == [dispatch_scenario_id]


@pytest.mark.asyncio
async def test_resolve_context_rejects_activity_outside_frozen_scope() -> None:
    company_id = uuid4()
    resolved_activity_id = uuid4()
    requested_activity_id = uuid4()
    resolved = SimpleNamespace(
        measurement=SimpleNamespace(id=uuid4(), calculation_run_id=uuid4()),
        activity=SimpleNamespace(id=resolved_activity_id),
        current_product=SimpleNamespace(id=uuid4()),
        method=None,
        rows_resolved=3,
    )
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(  # type: ignore[arg-type]
            workflow_resolver=_WorkflowResolver(resolved)
        )
    )
    context = _context(
        company_id=company_id,
        site_id=uuid4(),
        period_id=uuid4(),
        actor_id=uuid4(),
        modules=("measurement",),
        entities=(
            ContextEntity(
                entity_type="activity_record",
                entity_id=requested_activity_id,
            ),
        ),
        material_scope=("recycled aluminium",),
    )

    result = await registry.invoke(
        "resolve_context",
        context=context,
        payload={"include_metric_definitions": False},
    )

    assert result.status == "stale"
    assert result.code == "resolved_activity_scope_mismatch"


@pytest.mark.asyncio
async def test_measurement_handlers_only_replay_exact_verified_result() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    actor_id = uuid4()
    measurement_id = uuid4()
    activity_id = uuid4()
    method_id = uuid4()
    factor_id = uuid4()
    measurement = _measurement(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        measurement_id=measurement_id,
        activity_id=activity_id,
        method_id=method_id,
        factor_id=factor_id,
    )
    service = _MeasurementService(company_id, measurement)
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(
            measurement_service=service,  # type: ignore[arg-type]
        )
    )
    context = _context(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        actor_id=actor_id,
        modules=("measurement",),
        entities=(
            ContextEntity(entity_type="carbon_measurement", entity_id=measurement_id),
            ContextEntity(entity_type="activity_record", entity_id=activity_id),
        ),
        method_ids=(method_id,),
    )

    selected = await registry.invoke(
        "select_emission_factor",
        context=context,
        payload={
            "activity_record_ids": [str(activity_id)],
            "metric_key": "emissions.scope3.category1",
        },
    )
    calculated = await registry.invoke(
        "calculate_emissions",
        context=context,
        payload={
            "activity_record_ids": [str(activity_id)],
            "emission_factor_id": str(factor_id),
            "method_definition_id": str(method_id),
        },
    )
    stale = await registry.invoke(
        "calculate_emissions",
        context=context,
        payload={
            "activity_record_ids": [str(activity_id)],
            "emission_factor_id": str(uuid4()),
            "method_definition_id": str(method_id),
        },
    )

    assert selected.data["emission_factor_id"] == str(factor_id)
    assert calculated.status == "success"
    assert calculated.facts[0].fact_id == measurement.facts.fact_id
    assert stale.status == "stale"
    assert stale.code == "measurement_factor_mismatch"
    assert len(service.calls) == 3


@pytest.mark.asyncio
async def test_procurement_handlers_replay_existing_scenario_and_facts() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    actor_id = uuid4()
    method_id = uuid4()
    scenario_id = uuid4()
    measurement_id = uuid4()
    current_product_id = uuid4()
    candidate_id = uuid4()
    score_id = uuid4()
    recommendation_id = uuid4()
    ledger_event_id = uuid4()
    approval_id = uuid4()
    recommendation_summary = SimpleNamespace(
        id=recommendation_id,
        recommended_product_id=candidate_id,
    )
    assessment = SimpleNamespace(
        score_id=score_id,
        product=SimpleNamespace(id=candidate_id),
        impact=SimpleNamespace(
            projected_footprint_kgco2e=Decimal(800),
            avoided_kgco2e=Decimal(200),
            reduction_pct=Decimal(20),
            cost_delta_pct=Decimal(3),
        ),
    )
    scenario = SimpleNamespace(
        id=scenario_id,
        site_id=site_id,
        reporting_period_id=period_id,
        carbon_measurement_id=measurement_id,
        current_product=SimpleNamespace(
            id=current_product_id,
            material_code="recycled_aluminium",
        ),
        method=SimpleNamespace(id=method_id),
        constraints=SimpleNamespace(
            max_cost_increase_pct=Decimal(5),
            max_lead_time_days=20,
            minimum_circularity_score=Decimal(50),
            material=SimpleNamespace(
                allowed_material_codes=["recycled_aluminium"],
            ),
        ),
        alternatives=[assessment],
        selected_recommendation=recommendation_summary,
        terminal_state="completed",
    )
    recommendation = SimpleNamespace(
        id=recommendation_id,
        scenario_id=scenario_id,
        supplier_score=assessment,
        ledger_event_id=ledger_event_id,
        avoided_kgco2e=Decimal(200),
        cost_delta_pct=Decimal(3),
        payload_hash="a" * 64,
        analysis_signature="c" * 64,
        invalidated_at=None,
        status="pending_approval",
        approval=SimpleNamespace(
            id=approval_id,
            status="pending",
            preview_hash="a" * 64,
            analysis_signature="c" * 64,
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        ),
    )
    service = _ProcurementService(
        company_id=company_id,
        scenario=scenario,
        recommendation=recommendation,
    )
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(
            procurement_service=service,  # type: ignore[arg-type]
        )
    )
    context = _context(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        actor_id=actor_id,
        modules=("procurement",),
        entities=(
            ContextEntity(entity_type="current_product", entity_id=current_product_id),
            ContextEntity(entity_type="supplier_product", entity_id=candidate_id),
            ContextEntity(entity_type="procurement_scenario", entity_id=scenario_id),
        ),
        method_ids=(method_id,),
        material_scope=("recycled aluminium",),
        constraints=(
            ContextConstraint(key="max_cost_increase_pct", value="5"),
            ContextConstraint(key="max_lead_time_days", value="20"),
            ContextConstraint(key="minimum_circularity_score", value="50"),
        ),
    )

    loaded = await registry.invoke(
        "load_supplier_candidates",
        context=context,
        payload={"scenario_id": str(scenario_id), "limit": 20},
    )
    scored = await registry.invoke(
        "score_supplier",
        context=context,
        payload={
            "scenario_id": str(scenario_id),
            "supplier_product_ids": [str(candidate_id)],
            "method_definition_id": str(method_id),
        },
    )
    impact = await registry.invoke(
        "calculate_procurement_impact",
        context=context,
        payload={
            "scenario_id": str(scenario_id),
            "supplier_score_ids": [str(score_id)],
        },
    )
    built = await registry.invoke(
        "build_procurement_recommendation",
        context=context,
        payload={
            "scenario_id": str(scenario_id),
            "selected_supplier_score_id": str(score_id),
        },
    )
    preview = await registry.invoke(
        "create_approval_preview",
        context=context,
        payload={
            "target_type": "procurement_recommendation",
            "target_id": str(recommendation_id),
            "payload_hash": "a" * 64,
        },
    )
    service.preview_current = False
    stale_preview = await registry.invoke(
        "create_approval_preview",
        context=context,
        payload={
            "target_type": "procurement_recommendation",
            "target_id": str(recommendation_id),
            "payload_hash": "a" * 64,
        },
    )
    mismatched_context = context.model_copy(
        update={
            "constraints": (
                ContextConstraint(key="max_cost_increase_pct", value="4"),
                ContextConstraint(key="max_lead_time_days", value="20"),
                ContextConstraint(key="minimum_circularity_score", value="50"),
            )
        }
    )
    stale = await registry.invoke(
        "load_supplier_candidates",
        context=mismatched_context,
        payload={"scenario_id": str(scenario_id), "limit": 20},
    )
    scenario.terminal_state = "no_feasible_option"
    scenario.selected_recommendation = None
    no_feasible = await registry.invoke(
        "load_supplier_candidates",
        context=context,
        payload={"scenario_id": str(scenario_id), "limit": 20},
    )

    assert loaded.data["supplier_product_ids"] == [str(candidate_id)]
    assert scored.data["selected_supplier_score_id"] == str(score_id)
    assert impact.data["avoided_kgco2e"] == "200"
    assert built.status == "success"
    assert {fact.metric_key for fact in built.facts} == {
        "procurement.projected_avoided_emissions",
        "procurement.cost_delta_pct",
    }
    assert preview.status == "approval_required"
    assert preview.code == "human_approval_required"
    assert preview.data["approval_id"] == str(approval_id)
    assert preview.data["analysis_signature"] == "c" * 64
    assert service.preview_checks == [recommendation_id, recommendation_id]
    assert stale_preview.status == "stale"
    assert stale_preview.code == "approval_upstream_changed"
    assert stale.status == "stale"
    assert stale.code == "procurement_scenario_context_mismatch"
    assert no_feasible.status == "no_feasible_option"
    assert no_feasible.code == "no_feasible_supplier"


@pytest.mark.asyncio
async def test_candidate_listing_requires_frozen_scenario_before_scoring() -> None:
    company_id = uuid4()
    service = _ProcurementService(
        company_id=company_id,
        products=[SimpleNamespace(id=uuid4())],
    )
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(
            procurement_service=service,  # type: ignore[arg-type]
        )
    )
    context = _context(
        company_id=company_id,
        site_id=uuid4(),
        period_id=uuid4(),
        actor_id=uuid4(),
        modules=("procurement",),
        material_scope=("recycled aluminium",),
    )

    result = await registry.invoke(
        "load_supplier_candidates",
        context=context,
        payload={"material_code": "recycled aluminium", "limit": 20},
    )

    assert result.status == "needs_clarification"
    assert result.code == "procurement_scenario_required"
    assert result.data["required_fields"] == ["context.procurement_scenario_id"]
