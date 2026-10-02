from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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
from app.modules.agents.tools import AGENT_TOOL_NAMES, AgentToolContext, AgentToolRegistry


def _context(
    *,
    company_id: UUID,
    site_id: UUID,
    period_id: UUID,
    actor_id: UUID,
    modules: tuple[str, ...],
    entities: tuple[ContextEntity, ...],
    method_id: UUID | None = None,
) -> ContextEnvelope:
    return ContextEnvelope(
        company_id=company_id,
        site_id=site_id,
        reporting_period_id=period_id,
        actor_id=actor_id,
        actor_role="sustainability_analyst",
        modules=modules,
        metric_keys=("emissions.scope2.location_based",),
        entities=entities,
        method_definition_ids=((method_id,) if method_id is not None else ()),
        request_hash="1" * 64,
        analysis_signature="2" * 64,
    )


class _AssuranceService:
    def __init__(
        self,
        draft: SimpleNamespace,
        standard: SimpleNamespace,
        *,
        terminal_state: str = "approval_required",
    ) -> None:
        self.draft = draft
        self.standard = standard
        self.terminal_state = terminal_state
        self.validations = 0
        self.evidence_pack_reads = 0
        self.allowed_evidence_item_ids: frozenset[UUID] | None = None

    async def list_standards(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(items=[self.standard])

    async def get_draft(self, **_: object) -> SimpleNamespace:
        return self.draft

    async def validate_for_agent(
        self,
        *_: object,
        allowed_evidence_item_ids: frozenset[UUID] | None = None,
        **__: object,
    ) -> SimpleNamespace:
        self.validations += 1
        self.allowed_evidence_item_ids = allowed_evidence_item_ids
        return SimpleNamespace(terminal_state=self.terminal_state)

    async def get_evidence_pack(self, **_: object) -> object:
        self.evidence_pack_reads += 1
        return object()


class _DispatchService:
    def __init__(
        self,
        forecast: SimpleNamespace,
        recommendation: SimpleNamespace,
        scenario: SimpleNamespace,
    ) -> None:
        self.forecast = forecast
        self.recommendation = recommendation
        self.scenario = scenario
        self.optimizations = 0

    async def list_loads(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(items=[])

    async def sync_forecast(self, *_: object, **__: object) -> SimpleNamespace:
        return self.forecast

    async def get_scenario(self, **_: object) -> SimpleNamespace:
        return self.scenario

    async def optimize_scenario(self, scenario_id: UUID, request: object) -> SimpleNamespace:
        del request
        assert scenario_id == self.recommendation.scenario_id
        self.optimizations += 1
        return SimpleNamespace(
            terminal_state="approval_required",
            evaluated_windows=11,
            feasible_windows=4,
            recommendation=self.recommendation,
        )

    async def get_recommendation(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(
            terminal_state="approval_required",
            recommendation=self.recommendation,
        )


def _assurance_fixture() -> tuple[SimpleNamespace, SimpleNamespace, dict[str, UUID]]:
    ids = {
        name: uuid4()
        for name in (
            "standard",
            "requirement",
            "draft",
            "scope_claim",
            "claim",
            "binding",
            "ledger",
            "citation",
            "evidence",
            "approval",
        )
    }
    evidence = SimpleNamespace(
        id=ids["evidence"],
        evidence_type="document_chunk",
        similarity=Decimal("0.91"),
    )
    citation = SimpleNamespace(
        id=ids["citation"],
        validation_status="valid",
        evidence_item_id=ids["evidence"],
        evidence=evidence,
    )
    claim = SimpleNamespace(
        id=ids["claim"],
        requirement_id=ids["requirement"],
        fact_binding_id=ids["binding"],
        ledger_event_id=ids["ledger"],
        rendered_text="Plant B Scope 2 emissions are verified.",
        claim_template="Plant B Scope 2 emissions are verified.",
        validation_details={"metric_key": "emissions.scope2.location_based"},
        citations=[citation],
    )
    scope_claim = SimpleNamespace(
        id=ids["scope_claim"],
        requirement_id=ids["requirement"],
        fact_binding_id=None,
        ledger_event_id=None,
        rendered_text="Plant B Scope 2 emissions are verified.",
        claim_template="Plant B Scope 2 emissions are verified.",
        citations=[citation],
    )
    approval = SimpleNamespace(
        id=ids["approval"],
        target_type="disclosure_draft",
        target_id=ids["draft"],
        status="pending",
        preview_hash="a" * 64,
        analysis_signature="b" * 64,
        context_hash="c" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    standard = SimpleNamespace(
        id=ids["standard"],
        code="GHG-S2",
        name="GHG Protocol Scope 2",
        requirements=[SimpleNamespace(id=ids["requirement"], is_active=True)],
    )
    draft = SimpleNamespace(
        id=ids["draft"],
        standard=standard,
        site_id=None,
        reporting_period_id=None,
        measurement_id=None,
        context_hash="c" * 64,
        payload_hash="a" * 64,
        status="pending_approval",
        invalidated_at=None,
        validation_summary={
            "terminal_state": "approval_required",
            "analysis_signature": "b" * 64,
            "requirement_ids": [str(ids["requirement"])],
        },
        claims=[scope_claim, claim],
        gaps=[],
        approval=approval,
    )
    return draft, standard, ids


@pytest.mark.asyncio
async def test_assurance_tools_replay_one_domain_validation_transaction() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    actor_id = uuid4()
    draft, standard, ids = _assurance_fixture()
    draft.site_id = site_id
    draft.reporting_period_id = period_id
    service = _AssuranceService(draft, standard)
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(assurance_service=service)  # type: ignore[arg-type]
    )
    context = _context(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        actor_id=actor_id,
        modules=("assurance",),
        entities=(
            ContextEntity(entity_type="standard", entity_id=ids["standard"]),
            ContextEntity(entity_type="disclosure_draft", entity_id=ids["draft"]),
            ContextEntity(
                entity_type="disclosure_requirement",
                entity_id=ids["requirement"],
            ),
        ),
    )
    context = context.model_copy(
        update={
            "metric_keys": (
                "emissions.scope3.category1",
                "emissions.scope2.location_based",
            )
        }
    )

    mapped = await registry.invoke(
        "map_standard_requirement",
        context=context,
        payload={
            "standard_id": str(ids["standard"]),
            "requirement_ids": [str(ids["requirement"])],
        },
    )
    decomposed = await registry.invoke(
        "decompose_claim",
        context=context,
        payload={
            "disclosure_draft_id": str(ids["draft"]),
            "requirement_id": str(ids["requirement"]),
        },
    )
    retrieved = await registry.invoke(
        "retrieve_evidence",
        context=context,
        payload={
            "query_text": "Plant B Scope 2 emissions are verified.",
            "requirement_id": str(ids["requirement"]),
            "min_support_score": "0.8",
        },
    )
    bound = await registry.invoke(
        "bind_claim_facts",
        context=context,
        payload={
            "claim_id": str(ids["claim"]),
            "ledger_event_ids": [str(ids["ledger"])],
        },
    )
    citations = await registry.invoke(
        "validate_citations",
        context=context,
        payload={"claim_ids": [str(ids["claim"])]},
    )
    gaps = await registry.invoke(
        "detect_evidence_gaps",
        context=context,
        payload={
            "disclosure_draft_id": str(ids["draft"]),
            "claim_ids": [str(ids["claim"])],
        },
    )
    preview = await registry.invoke(
        "create_approval_preview",
        context=context,
        payload={
            "target_type": "disclosure_draft",
            "target_id": str(ids["draft"]),
            "payload_hash": "a" * 64,
        },
    )

    assert mapped.status == "success"
    assert decomposed.data["claim_id"] == str(ids["claim"])
    assert decomposed.data["claim_id"] != str(ids["scope_claim"])
    assert retrieved.data["evidence_item_ids"] == [str(ids["evidence"])]
    assert bound.facts[0].fact_id == ids["binding"]
    assert bound.facts[0].metric_key == "emissions.scope2.location_based"
    assert citations.status == "success"
    assert gaps.data["approval_target_id"] == str(ids["draft"])
    assert preview.status == "approval_required"
    assert preview.data["approval_context_hash"] == "c" * 64
    assert service.validations == 1
    assert service.evidence_pack_reads == 1


@pytest.mark.asyncio
async def test_assurance_validation_freezes_evidence_allowlist_before_claim_creation() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    draft, standard, ids = _assurance_fixture()
    draft.site_id = site_id
    draft.reporting_period_id = period_id
    persisted_claims = draft.claims
    draft.claims = []

    class PersistingAssuranceService(_AssuranceService):
        async def validate_for_agent(
            self,
            *_: object,
            allowed_evidence_item_ids: frozenset[UUID] | None = None,
            **__: object,
        ) -> SimpleNamespace:
            result = await super().validate_for_agent(
                allowed_evidence_item_ids=allowed_evidence_item_ids
            )
            self.draft.claims = persisted_claims
            return result

    service = PersistingAssuranceService(draft, standard)
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(assurance_service=service)  # type: ignore[arg-type]
    )
    context = _context(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        actor_id=uuid4(),
        modules=("assurance",),
        entities=(
            ContextEntity(entity_type="standard", entity_id=ids["standard"]),
            ContextEntity(entity_type="disclosure_draft", entity_id=ids["draft"]),
            ContextEntity(
                entity_type="disclosure_requirement",
                entity_id=ids["requirement"],
            ),
            ContextEntity(entity_type="evidence_item", entity_id=ids["evidence"]),
        ),
    )

    result = await registry.invoke(
        "decompose_claim",
        context=context,
        payload={
            "disclosure_draft_id": str(ids["draft"]),
            "requirement_id": str(ids["requirement"]),
        },
    )

    assert result.status == "success"
    assert service.validations == 1
    assert service.allowed_evidence_item_ids == frozenset({ids["evidence"]})


@pytest.mark.asyncio
async def test_assurance_decomposition_propagates_blocked_domain_result_as_unsupported() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    draft, standard, ids = _assurance_fixture()
    draft.site_id = site_id
    draft.reporting_period_id = period_id
    draft.status = "blocked"
    draft.approval = None
    draft.validation_summary = {
        "terminal_state": "unsupported",
        "requirement_ids": [str(ids["requirement"])],
    }
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(
            assurance_service=_AssuranceService(
                draft,
                standard,
                terminal_state="unsupported",
            )  # type: ignore[arg-type]
        )
    )
    context = _context(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        actor_id=uuid4(),
        modules=("assurance",),
        entities=(
            ContextEntity(entity_type="standard", entity_id=ids["standard"]),
            ContextEntity(entity_type="disclosure_draft", entity_id=ids["draft"]),
        ),
    )

    result = await registry.invoke(
        "decompose_claim",
        context=context,
        payload={
            "disclosure_draft_id": str(ids["draft"]),
            "requirement_id": str(ids["requirement"]),
        },
    )

    assert result.status == "unsupported"
    assert result.code == "assurance_required_claim_unsupported"
    assert result.data["disclosure_draft_id"] == str(ids["draft"])


@pytest.mark.asyncio
async def test_assurance_tool_rejects_requirement_subset_outside_frozen_draft() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    draft, standard, ids = _assurance_fixture()
    draft.site_id = site_id
    draft.reporting_period_id = period_id
    service = _AssuranceService(draft, standard)
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(assurance_service=service)  # type: ignore[arg-type]
    )
    context = _context(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        actor_id=uuid4(),
        modules=("assurance",),
        entities=(
            ContextEntity(entity_type="standard", entity_id=ids["standard"]),
            ContextEntity(entity_type="disclosure_draft", entity_id=ids["draft"]),
            ContextEntity(entity_type="disclosure_requirement", entity_id=uuid4()),
        ),
    )

    result = await registry.invoke(
        "decompose_claim",
        context=context,
        payload={
            "disclosure_draft_id": str(ids["draft"]),
            "requirement_id": str(ids["requirement"]),
        },
    )

    assert result.status == "stale"
    assert result.code == "assurance_draft_context_mismatch"
    assert service.validations == 0


@pytest.mark.asyncio
async def test_assurance_tool_rejects_citation_outside_frozen_evidence_scope() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    draft, standard, ids = _assurance_fixture()
    draft.site_id = site_id
    draft.reporting_period_id = period_id
    service = _AssuranceService(draft, standard)
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(assurance_service=service)  # type: ignore[arg-type]
    )
    context = _context(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        actor_id=uuid4(),
        modules=("assurance",),
        entities=(
            ContextEntity(entity_type="standard", entity_id=ids["standard"]),
            ContextEntity(entity_type="disclosure_draft", entity_id=ids["draft"]),
            ContextEntity(
                entity_type="disclosure_requirement",
                entity_id=ids["requirement"],
            ),
            ContextEntity(entity_type="evidence_item", entity_id=uuid4()),
        ),
    )

    result = await registry.invoke(
        "decompose_claim",
        context=context,
        payload={
            "disclosure_draft_id": str(ids["draft"]),
            "requirement_id": str(ids["requirement"]),
        },
    )

    assert result.status == "stale"
    assert result.code == "assurance_draft_context_mismatch"
    assert service.validations == 0


def test_measurement_scope_rejects_frozen_method_mismatch() -> None:
    expected_method_id = uuid4()
    context = _context(
        company_id=uuid4(),
        site_id=uuid4(),
        period_id=uuid4(),
        actor_id=uuid4(),
        modules=("measurement",),
        entities=(),
        method_id=expected_method_id,
    )
    measurement = SimpleNamespace(
        site_id=context.site_id,
        reporting_period_id=context.reporting_period_id,
        calculation_run=SimpleNamespace(method_definition_id=uuid4()),
    )

    result = CarbonMeshAgentToolServicePort._measurement_scope_failure(
        context,
        measurement,  # type: ignore[arg-type]
    )

    assert result is not None
    assert result.status == "stale"
    assert result.code == "measurement_context_mismatch"


@pytest.mark.asyncio
async def test_multi_module_procurement_method_does_not_constrain_other_modules() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    scenario_id = uuid4()
    procurement_method_id = uuid4()
    measurement_method_id = uuid4()
    dispatch_method_id = uuid4()
    context = _context(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        actor_id=uuid4(),
        modules=("measurement", "assurance", "procurement", "dispatch"),
        entities=(ContextEntity(entity_type="dispatch_scenario", entity_id=scenario_id),),
        method_id=procurement_method_id,
    )
    tool_context = AgentToolContext.from_envelope(context)
    measurement = SimpleNamespace(
        site_id=site_id,
        reporting_period_id=period_id,
        calculation_run=SimpleNamespace(method_definition_id=measurement_method_id),
    )
    scenario = SimpleNamespace(
        id=scenario_id,
        site_id=site_id,
        flexible_load=SimpleNamespace(
            id=uuid4(),
            duration_minutes=120,
            maximum_power_kw=Decimal(500),
        ),
        method=SimpleNamespace(id=dispatch_method_id),
        policy_definition_id=None,
        forecast_source_document_id=uuid4(),
        window_start=datetime(2026, 10, 1, 8, tzinfo=UTC),
        window_end=datetime(2026, 10, 1, 20, tzinfo=UTC),
        constraints=SimpleNamespace(
            maximum_delay_minutes=240,
            source_constraints=[],
        ),
    )
    port = CarbonMeshAgentToolServicePort(
        dispatch_service=_DispatchService(
            SimpleNamespace(),
            SimpleNamespace(scenario_id=scenario_id),
            scenario,
        )  # type: ignore[arg-type]
    )

    measurement_failure = port._measurement_scope_failure(
        tool_context,
        measurement,  # type: ignore[arg-type]
    )
    resolved_scenario, dispatch_failure = await port._dispatch_scenario(
        tool_context,
        scenario_id,
    )

    assert measurement_failure is None
    assert dispatch_failure is None
    assert resolved_scenario is scenario


@pytest.mark.asyncio
async def test_dispatch_tools_validate_forecast_optimizer_impact_and_preview() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    actor_id = uuid4()
    method_id = uuid4()
    scenario_id = uuid4()
    forecast_id = uuid4()
    recommendation_id = uuid4()
    ledger_id = uuid4()
    approval = SimpleNamespace(
        id=uuid4(),
        target_type="dispatch_recommendation",
        target_id=recommendation_id,
        status="pending",
        preview_hash="d" * 64,
        analysis_signature="e" * 64,
        context_hash="f" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    recommendation = SimpleNamespace(
        id=recommendation_id,
        scenario_id=scenario_id,
        status="pending_approval",
        recommended_start=datetime(2026, 7, 1, 2, tzinfo=UTC),
        recommended_end=datetime(2026, 7, 1, 4, tzinfo=UTC),
        expected_emissions_kgco2e=Decimal(100),
        baseline_emissions_kgco2e=Decimal(150),
        avoided_kgco2e=Decimal(50),
        reduction_pct=Decimal("33.3333"),
        impact_snapshot={
            "method_id": str(method_id),
            "forecast_source_document_id": str(forecast_id),
        },
        analysis_signature="e" * 64,
        payload_hash="d" * 64,
        ledger_event_id=ledger_id,
        evidence_item_ids=[uuid4()],
        approval=approval,
        invalidated_at=None,
    )
    forecast_start = datetime(2026, 7, 1, tzinfo=UTC)
    forecast = SimpleNamespace(
        source_document_id=forecast_id,
        forecast_start=forecast_start,
        forecast_end=forecast_start + timedelta(hours=24),
        zone="IN",
        snapshot_hash="9" * 64,
        source_mode="fixture",
        inserted_points=24,
        existing_points=0,
        points=[object()] * 24,
    )
    scenario = SimpleNamespace(
        id=scenario_id,
        site_id=site_id,
        flexible_load=SimpleNamespace(
            id=uuid4(),
            duration_minutes=120,
            maximum_power_kw=Decimal(500),
        ),
        method=SimpleNamespace(id=method_id),
        policy_definition_id=None,
        forecast_source_document_id=forecast_id,
        window_start=forecast_start,
        window_end=forecast_start + timedelta(hours=12),
        constraints=SimpleNamespace(
            maximum_delay_minutes=240,
            source_constraints=[],
        ),
    )
    service = _DispatchService(forecast, recommendation, scenario)
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(
            dispatch_service=service,  # type: ignore[arg-type]
            fixture_grid_provider=object(),  # type: ignore[arg-type]
        )
    )
    context = _context(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        actor_id=actor_id,
        modules=("dispatch",),
        entities=(ContextEntity(entity_type="dispatch_scenario", entity_id=scenario_id),),
        method_id=method_id,
    )

    synced = await registry.invoke(
        "sync_grid_forecast",
        context=context.model_copy(update={"constraints": (
            *context.constraints,
            ContextConstraint(key="integration.grid_source_mode", value="fixture"),
        )}),
        payload={"forecast_start": forecast_start.isoformat(), "source_mode": "fixture"},
    )
    optimized = await registry.invoke(
        "optimize_dispatch_window",
        context=context,
        payload={
            "scenario_id": str(scenario_id),
            "forecast_id": str(forecast_id),
            "method_definition_id": str(method_id),
        },
    )
    impact = await registry.invoke(
        "calculate_dispatch_impact",
        context=context,
        payload={
            "scenario_id": str(scenario_id),
            "recommendation_id": str(recommendation_id),
        },
    )
    preview = await registry.invoke(
        "create_approval_preview",
        context=context,
        payload={
            "target_type": "dispatch_recommendation",
            "target_id": str(recommendation_id),
            "payload_hash": "d" * 64,
        },
    )

    assert synced.data["forecast_id"] == str(forecast_id)
    assert optimized.data["dispatch_recommendation_id"] == str(recommendation_id)
    assert {item.metric_key for item in impact.facts} == {
        "dispatch.avoided_emissions",
        "dispatch.expected_emissions",
    }
    assert preview.status == "approval_required"
    assert preview.data["approval_context_hash"] == "f" * 64
    assert service.optimizations == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch",
    [
        "site",
        "flexible_load",
        "policy",
        "forecast",
        "method",
        "window_start",
        "window_end",
        "duration_minutes",
        "max_delay_minutes",
        "maximum_power_kw",
        "blackout_constraint_ids",
    ],
)
@pytest.mark.parametrize(
    "tool_name",
    ["optimize_dispatch_window", "create_approval_preview"],
)
async def test_dispatch_tools_reject_frozen_scenario_mismatch_before_replay(
    mismatch: str,
    tool_name: str,
) -> None:
    company_id = uuid4()
    site_id = uuid4()
    scenario_id = uuid4()
    recommendation_id = uuid4()
    forecast_id = uuid4()
    method_id = uuid4()
    flexible_load_id = uuid4()
    policy_id = uuid4()
    blackout_id = uuid4()
    start = datetime(2026, 10, 1, 8, tzinfo=UTC)
    end = datetime(2026, 10, 1, 20, tzinfo=UTC)
    recommendation = SimpleNamespace(
        id=recommendation_id,
        scenario_id=scenario_id,
        impact_snapshot={
            "method_id": str(method_id),
            "forecast_source_document_id": str(forecast_id),
        },
    )
    scenario = SimpleNamespace(
        id=scenario_id,
        site_id=site_id,
        flexible_load=SimpleNamespace(
            id=flexible_load_id,
            duration_minutes=120,
            maximum_power_kw=Decimal(500),
        ),
        method=SimpleNamespace(id=method_id),
        policy_definition_id=policy_id,
        forecast_source_document_id=forecast_id,
        window_start=start,
        window_end=end,
        constraints=SimpleNamespace(
            maximum_delay_minutes=240,
            source_constraints=[
                SimpleNamespace(
                    id=blackout_id,
                    is_hard=True,
                    constraint_type="blackout",
                )
            ],
        ),
    )
    service = _DispatchService(SimpleNamespace(), recommendation, scenario)
    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(dispatch_service=service)  # type: ignore[arg-type]
    )
    entities = [
        ContextEntity(entity_type="dispatch_scenario", entity_id=scenario_id),
        ContextEntity(entity_type="flexible_load", entity_id=flexible_load_id),
        ContextEntity(entity_type="policy_definition", entity_id=policy_id),
        ContextEntity(entity_type="grid_forecast", entity_id=forecast_id),
    ]
    constraints = {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "duration_minutes": "120",
        "max_delay_minutes": "240",
        "maximum_power_kw": "500",
        "blackout_constraint_ids": json.dumps([str(blackout_id)]),
    }
    scoped_site_id = site_id
    if mismatch == "site":
        scoped_site_id = uuid4()
    elif mismatch == "flexible_load":
        entities[1] = ContextEntity(entity_type="flexible_load", entity_id=uuid4())
    elif mismatch == "policy":
        entities[2] = ContextEntity(entity_type="policy_definition", entity_id=uuid4())
    elif mismatch == "forecast":
        entities[3] = ContextEntity(entity_type="grid_forecast", entity_id=uuid4())
    elif mismatch == "method":
        method_id = uuid4()
    elif mismatch == "window_start":
        constraints[mismatch] = (start + timedelta(hours=1)).isoformat()
    elif mismatch == "window_end":
        constraints[mismatch] = (end - timedelta(hours=1)).isoformat()
    elif mismatch == "duration_minutes":
        constraints[mismatch] = "60"
    elif mismatch == "max_delay_minutes":
        constraints[mismatch] = "180"
    elif mismatch == "maximum_power_kw":
        constraints[mismatch] = "499"
    else:
        constraints[mismatch] = json.dumps([str(uuid4())])
    context = ContextEnvelope(
        company_id=company_id,
        site_id=scoped_site_id,
        reporting_period_id=None,
        actor_id=uuid4(),
        actor_role="operations_planner",
        modules=("dispatch",),
        metric_keys=("dispatch.avoided_emissions",),
        entities=tuple(entities),
        method_definition_ids=(method_id,),
        constraints=tuple(
            ContextConstraint(key=f"dispatch.{key}", value=value)
            for key, value in constraints.items()
        ),
        request_hash="1" * 64,
        analysis_signature="2" * 64,
    )
    payload = (
        {
            "scenario_id": str(scenario_id),
            "forecast_id": str(forecast_id),
            "method_definition_id": str(method_id),
        }
        if tool_name == "optimize_dispatch_window"
        else {
            "target_type": "dispatch_recommendation",
            "target_id": str(recommendation_id),
            "payload_hash": "d" * 64,
        }
    )

    result = await registry.invoke(tool_name, context=context, payload=payload)

    assert result.status == "stale"
    assert result.code == "dispatch_scenario_context_mismatch"
    assert service.optimizations == 0


@pytest.mark.asyncio
async def test_shared_history_and_ledger_adapters_are_typed_and_replayable() -> None:
    company_id = uuid4()
    site_id = uuid4()
    period_id = uuid4()
    actor_id = uuid4()
    source_document_id = uuid4()
    ledger_event_id = uuid4()
    history_calls = 0
    ledger_calls = 0

    async def sync_history(context: object, arguments: object) -> SimpleNamespace:
        nonlocal history_calls
        history_calls += 1
        return SimpleNamespace(
            site_id=site_id,
            zone="IN",
            requested_start=arguments.start,
            requested_end=arguments.end,
            source_document_id=source_document_id,
            data_source_id=uuid4(),
            response_checksum="7" * 64,
            received_points=2,
            inserted_points=2,
            existing_points=0,
        )

    async def write_event(context: object, arguments: object) -> tuple[UUID, str]:
        nonlocal ledger_calls
        ledger_calls += 1
        return ledger_event_id, "8" * 64

    registry = AgentToolRegistry(
        CarbonMeshAgentToolServicePort(
            grid_history_sync=sync_history,  # type: ignore[arg-type]
            ledger_event_write=write_event,  # type: ignore[arg-type]
        )
    )
    context = _context(
        company_id=company_id,
        site_id=site_id,
        period_id=period_id,
        actor_id=actor_id,
        modules=("measurement",),
        entities=(),
    )
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = start + timedelta(hours=2)

    history = await registry.invoke(
        "sync_grid_history",
        context=context,
        payload={"start": start.isoformat(), "end": end.isoformat()},
    )
    ledger = await registry.invoke(
        "write_ledger_event",
        context=context,
        payload={
            "event_type": "agent.measurement_observed",
            "subject_type": "carbon_measurement",
            "subject_id": str(uuid4()),
            "payload_hash": "6" * 64,
        },
    )

    assert history.rows == 2
    assert history.data["source_document_id"] == str(source_document_id)
    assert ledger.data["ledger_event_id"] == str(ledger_event_id)
    assert history_calls == ledger_calls == 1


@pytest.mark.asyncio
async def test_direct_ledger_write_stays_unavailable_without_person2_adapter() -> None:
    registry = AgentToolRegistry(CarbonMeshAgentToolServicePort())
    context = _context(
        company_id=uuid4(),
        site_id=uuid4(),
        period_id=uuid4(),
        actor_id=uuid4(),
        modules=("measurement",),
        entities=(),
    )

    result = await registry.invoke(
        "write_ledger_event",
        context=context,
        payload={
            "event_type": "agent.measurement_observed",
            "subject_type": "carbon_measurement",
            "subject_id": str(uuid4()),
            "payload_hash": "6" * 64,
        },
    )

    assert result.status == "unsupported"
    assert result.code == "tool_handler_unavailable"


def test_fully_composed_port_registers_all_frozen_tool_ids() -> None:
    port = CarbonMeshAgentToolServicePort(
        measurement_service=object(),  # type: ignore[arg-type]
        procurement_service=object(),  # type: ignore[arg-type]
        assurance_service=object(),  # type: ignore[arg-type]
        dispatch_service=object(),  # type: ignore[arg-type]
    )

    assert all(port.handler_for(tool_name) is not None for tool_name in AGENT_TOOL_NAMES)
