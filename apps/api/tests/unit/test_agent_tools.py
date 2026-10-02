from __future__ import annotations

from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.modules.agents.context import build_frozen_context, freeze_runtime_context
from app.modules.agents.graph_adapter import RegistryGraphToolInvoker, default_tool_payload
from app.modules.agents.graph_contracts import (
    ContextConstraint,
    ContextEntity,
    ContextEnvelope,
    ToolInvocation,
    ToolOutput,
)
from app.modules.agents.planning import PlannerSelection, build_execution_plan
from app.modules.agents.schemas import AgentContextRequest
from app.modules.agents.tools import (
    AGENT_TOOL_DEFINITIONS,
    AGENT_TOOL_NAMES,
    AgentToolContext,
    AgentToolContextOverrideError,
    AgentToolHandler,
    AgentToolPayloadError,
    AgentToolRegistry,
    AgentToolResult,
    AgentToolResultError,
    AgentToolServicePort,
    ResolveEntityInput,
    RetrieveLedgerFactsInput,
    StrictToolInput,
    UnknownAgentToolError,
)

EXPECTED_TOOL_MODELS = {
    "resolve_context": "ResolveContextInput",
    "resolve_entity": "ResolveEntityInput",
    "retrieve_ledger_facts": "RetrieveLedgerFactsInput",
    "retrieve_evidence": "RetrieveEvidenceInput",
    "write_ledger_event": "WriteLedgerEventInput",
    "create_approval_preview": "CreateApprovalPreviewInput",
    "validate_activity": "ValidateActivityInput",
    "normalize_unit": "NormalizeUnitInput",
    "sync_grid_history": "SyncGridHistoryInput",
    "select_emission_factor": "SelectEmissionFactorInput",
    "calculate_emissions": "CalculateEmissionsInput",
    "calculate_confidence": "CalculateConfidenceInput",
    "map_standard_requirement": "MapStandardRequirementInput",
    "decompose_claim": "DecomposeClaimInput",
    "bind_claim_facts": "BindClaimFactsInput",
    "validate_citations": "ValidateCitationsInput",
    "detect_evidence_gaps": "DetectEvidenceGapsInput",
    "load_supplier_candidates": "LoadSupplierCandidatesInput",
    "score_supplier": "ScoreSupplierInput",
    "calculate_procurement_impact": "CalculateProcurementImpactInput",
    "build_procurement_recommendation": "BuildProcurementRecommendationInput",
    "sync_grid_forecast": "SyncGridForecastInput",
    "optimize_dispatch_window": "OptimizeDispatchWindowInput",
    "calculate_dispatch_impact": "CalculateDispatchImpactInput",
}


def _context():
    request = AgentContextRequest(
        company_id=uuid4(),
        actor_id=uuid4(),
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        material_scope=["recycled aluminium"],
        constraints={
            "max_cost_increase_pct": "5",
            "max_lead_time_days": 20,
            "minimum_circularity_score": "50",
        },
    )
    return build_frozen_context(
        request_context=request,
        actor_role="procurement_manager",
        query="Compare verified supplier products.",
        workflow="procurement",
    )


@pytest.mark.parametrize("source_mode", ["live", "fixture"])
@pytest.mark.parametrize("tool_name", ["sync_grid_history", "sync_grid_forecast"])
def test_grid_mode_is_frozen_and_cannot_be_changed_by_prior_outputs(source_mode, tool_name):
    request = AgentContextRequest(company_id=uuid4(), actor_id=uuid4())
    plan = build_execution_plan(PlannerSelection(
        disposition="execute", modules=("dispatch",), reason_code="dispatch_request",
    ))
    live_api, live_graph = freeze_runtime_context(
        request_context=request, actor_role="operations_planner", query="Plan dispatch.", plan=plan,
    )
    api, graph = freeze_runtime_context(
        request_context=request.model_copy(update={"grid_source_mode": source_mode}),
        actor_role="operations_planner", query="Plan dispatch.", plan=plan,
    )
    assert request.grid_source_mode == "live"
    assert api.grid_source_mode == source_mode
    assert (graph.analysis_signature == live_graph.analysis_signature) == (source_mode == "live")
    assert (api.analysis_signature == live_api.analysis_signature) == (source_mode == "live")
    invocation = ToolInvocation(
        run_id=uuid4(), graph_name="dispatch", module="dispatch", stage_id="dispatch.sync",
        tool_name=tool_name, context=graph, checkpoint_revision=0,
        prior_outputs=(
            ToolOutput(key="grid_source_mode", value="fixture" if source_mode == "live" else "live"),
            ToolOutput(key="forecast_source_mode", value="fixture" if source_mode == "live" else "live"),
        ),
    )
    assert default_tool_payload(invocation)["source_mode"] == source_mode


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,payload", [
    ("sync_grid_forecast", {"forecast_start": "2026-10-02T00:00:00Z"}),
    ("sync_grid_history", {"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"}),
])
async def test_grid_tool_cannot_switch_live_context_to_fixture(tool_name, payload):
    service = FakeServicePort()
    with pytest.raises(AgentToolContextOverrideError):
        await AgentToolRegistry(service).invoke(
            tool_name, context=_context(), payload={**payload, "source_mode": "fixture"},
        )
    assert service.lookups == []


class FakeServicePort:
    def __init__(self, handlers: dict[str, AgentToolHandler] | None = None) -> None:
        self.handlers = handlers or {}
        self.lookups: list[str] = []

    def handler_for(self, tool_name):
        self.lookups.append(tool_name)
        return self.handlers.get(tool_name)


def test_frozen_allowlist_has_exactly_24_strict_typed_definitions() -> None:
    assert tuple(AGENT_TOOL_DEFINITIONS) == AGENT_TOOL_NAMES
    assert len(AGENT_TOOL_NAMES) == 24
    assert set(AGENT_TOOL_NAMES) == set(EXPECTED_TOOL_MODELS)

    forbidden_fields = {
        "company_id",
        "actor_id",
        "site_id",
        "reporting_period_id",
        "analysis_signature",
        "constraints",
        "sql",
        "session",
        "credentials",
        "decision",
    }
    for tool_name, definition in AGENT_TOOL_DEFINITIONS.items():
        assert definition.tool_id == tool_name
        assert definition.name == tool_name
        assert definition.input_model.__name__ == EXPECTED_TOOL_MODELS[tool_name]
        assert issubclass(definition.input_model, StrictToolInput)
        assert definition.input_model.model_config["extra"] == "forbid"
        assert definition.input_model.model_config["frozen"] is True
        assert forbidden_fields.isdisjoint(definition.input_model.model_fields)


@pytest.mark.asyncio
async def test_registry_validates_payload_runs_guard_then_dispatches_typed_service() -> None:
    context = _context()
    order: list[str] = []
    captured_context: AgentToolContext | None = None
    captured_arguments: StrictToolInput | None = None

    async def handler(*, context, arguments):
        nonlocal captured_context, captured_arguments
        order.append("handler")
        captured_context = context
        captured_arguments = arguments
        return AgentToolResult(
            status="success",
            data={"fact_ids": [str(uuid4())]},
            rows=2,
            chunks=1,
            cache={"hit": True, "cache_name": "ledger-facts"},
        )

    async def guard(definition, safe_context, arguments):
        order.append("guard")
        assert definition.tool_id == "retrieve_ledger_facts"
        assert safe_context.company_id == context.company_id
        assert isinstance(arguments, RetrieveLedgerFactsInput)

    service = FakeServicePort({"retrieve_ledger_facts": handler})
    result = await AgentToolRegistry(service).invoke(
        "retrieve_ledger_facts",
        context=context,
        payload={"metric_keys": ["emissions.scope3.category1"], "limit": 2},
        before_dispatch=guard,
    )

    assert order == ["guard", "handler"]
    assert service.lookups == ["retrieve_ledger_facts"]
    assert captured_context is not None
    assert captured_context.company_id == context.company_id
    assert captured_context.analysis_signature == context.analysis_signature
    assert captured_context.constraints == {
        **context.constraints.model_dump(mode="json"),
        "integration.grid_source_mode": "live",
    }
    assert isinstance(captured_arguments, RetrieveLedgerFactsInput)
    assert captured_arguments.metric_keys == ("emissions.scope3.category1",)
    assert result.status == "success"
    assert result.rows == 2
    assert result.chunks == 1
    assert result.cache.hit is True


@pytest.mark.asyncio
async def test_registry_accepts_the_new_immutable_graph_context_contract() -> None:
    company_id = uuid4()
    product_id = uuid4()

    async def handler(*, context, arguments):
        assert context.company_id == company_id
        assert context.modules == ("procurement",)
        assert context.supplier_product_ids == (product_id,)
        assert context.constraints == {"max_cost_increase_pct": "5"}
        return AgentToolResult(status="success", data={"resolved": True})

    graph_context = ContextEnvelope(
        company_id=company_id,
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        actor_id=uuid4(),
        actor_role="procurement_manager",
        modules=("procurement",),
        entities=(ContextEntity(entity_type="supplier_product", entity_id=product_id),),
        constraints=(ContextConstraint(key="max_cost_increase_pct", value="5"),),
        request_hash="1" * 64,
        analysis_signature="2" * 64,
    )
    service = FakeServicePort({"resolve_context": handler})

    result = await AgentToolRegistry(service).invoke(
        "resolve_context",
        context=graph_context,
        payload={},
    )

    assert result.status == "success"


def test_graph_context_keeps_current_product_out_of_candidate_allowlist() -> None:
    current_product_id = uuid4()
    candidate_product_id = uuid4()
    request = AgentContextRequest(
        company_id=uuid4(),
        actor_id=uuid4(),
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        current_product_id=current_product_id,
        supplier_product_ids=[candidate_product_id],
        material_scope=["packaging tray"],
        constraints={
            "max_cost_increase_pct": "5",
            "max_lead_time_days": 30,
            "minimum_circularity_score": "0",
        },
    )
    plan = build_execution_plan(
        PlannerSelection(
            disposition="execute",
            modules=("measurement", "procurement"),
            reason_code="cross_module_request",
        )
    )

    _, graph_context = freeze_runtime_context(
        request_context=request,
        actor_role="procurement_manager",
        query="Measure and compare supplier products.",
        plan=plan,
    )
    tool_context = AgentToolContext.from_envelope(graph_context)

    assert tool_context.current_product_id == current_product_id
    assert tool_context.supplier_product_ids == (candidate_product_id,)
    assert {
        (entity.entity_type, entity.entity_id) for entity in graph_context.entities
    } >= {
        ("current_product", current_product_id),
        ("supplier_product", candidate_product_id),
    }


@pytest.mark.asyncio
async def test_registry_rejects_unknown_tool_before_guard_or_service_lookup() -> None:
    guard_called = False
    service = FakeServicePort()

    def guard(*_):
        nonlocal guard_called
        guard_called = True

    with pytest.raises(UnknownAgentToolError, match="not allowlisted"):
        await AgentToolRegistry(service).invoke(
            "execute_sql",
            context=_context(),
            payload={},
            before_dispatch=guard,
        )

    assert guard_called is False
    assert service.lookups == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"company_id": str(uuid4())},
        {"actor_id": str(uuid4())},
        {"constraints": {"max_cost_increase_pct": "100"}},
        {"analysis_signature": "0" * 64},
    ],
)
async def test_registry_rejects_frozen_context_overrides(payload: dict[str, object]) -> None:
    service = FakeServicePort()

    with pytest.raises(AgentToolContextOverrideError):
        await AgentToolRegistry(service).invoke(
            "resolve_context",
            context=_context(),
            payload=payload,
        )

    assert service.lookups == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"sql": "SELECT * FROM core.companies"},
        {"credentials": "provider-secret"},
        {"session": object()},
        {"decision": "approve"},
    ],
)
async def test_registry_rejects_sql_credentials_sessions_and_approval_decisions(
    payload: dict[str, object],
) -> None:
    service = FakeServicePort()

    with pytest.raises(AgentToolPayloadError):
        await AgentToolRegistry(service).invoke(
            "resolve_context",
            context=_context(),
            payload=payload,
        )

    assert service.lookups == []


@pytest.mark.asyncio
async def test_missing_service_handler_returns_typed_unsupported_after_guard() -> None:
    calls: list[str] = []

    def guard(*_):
        calls.append("guard")

    service = FakeServicePort()
    result = await AgentToolRegistry(service).invoke(
        "resolve_context",
        context=_context(),
        payload={},
        before_dispatch=guard,
    )

    assert calls == ["guard"]
    assert service.lookups == ["resolve_context"]
    assert result == AgentToolResult(
        status="unsupported",
        code="tool_handler_unavailable",
        message="The resolve_context service handler is unavailable.",
    )
    assert result.rows == 0
    assert result.chunks == 0
    assert result.cache.hit is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["provider_unavailable", "policy_blocked", "budget_exhausted"],
)
async def test_graph_adapter_preserves_terminal_tool_status(status: str) -> None:
    async def handler(*, context, arguments):
        del context, arguments
        return AgentToolResult(status=status, code=f"{status}_code")  # type: ignore[arg-type]

    context = ContextEnvelope(
        company_id=uuid4(),
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        actor_id=uuid4(),
        actor_role="sustainability_analyst",
        modules=("measurement",),
        request_hash="1" * 64,
        analysis_signature="2" * 64,
    )
    invocation = ToolInvocation(
        run_id=uuid4(),
        graph_name="orchestrator",
        module=None,
        stage_id="context.resolve",
        tool_name="resolve_context",
        context=context,
        checkpoint_revision=0,
    )
    adapter = RegistryGraphToolInvoker(
        AgentToolRegistry(FakeServicePort({"resolve_context": handler}))  # type: ignore[arg-type]
    )

    result = await adapter.invoke(invocation)

    assert result.status == status
    assert result.code == f"{status}_code"


@pytest.mark.asyncio
@pytest.mark.parametrize("typed_payload", [False, True])
async def test_graph_adapter_dispatches_mapping_and_typed_payloads(typed_payload: bool) -> None:
    calls = []

    async def handler(*, context, arguments):
        calls.append(arguments)
        return AgentToolResult(status="success")

    context = ContextEnvelope(
        company_id=uuid4(),
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        actor_id=uuid4(),
        actor_role="sustainability_analyst",
        modules=("measurement",),
        request_hash="1" * 64,
        analysis_signature="2" * 64,
    )
    invocation = ToolInvocation(
        run_id=uuid4(),
        graph_name="orchestrator",
        module=None,
        stage_id="context.resolve",
        tool_name="resolve_entity",
        context=context,
        checkpoint_revision=0,
    )
    payload = ResolveEntityInput(entity_type="site", reference="current")
    adapter = RegistryGraphToolInvoker(
        AgentToolRegistry(FakeServicePort({"resolve_entity": handler})),
        payload_factory=lambda _: payload if typed_payload else payload.model_dump(),
    )

    result = await adapter.invoke(invocation)

    assert result.status == "success"
    assert calls == [payload]


@pytest.mark.asyncio
async def test_graph_adapter_keeps_run_and_domain_approval_context_hashes_separate() -> None:
    target_id = uuid4()
    approval_id = uuid4()

    async def handler(*, context, arguments):
        del context, arguments
        return AgentToolResult(
            status="approval_required",
            data={
                "approval_id": str(approval_id),
                "target_type": "dispatch_recommendation",
                "target_id": str(target_id),
                "preview_hash": "3" * 64,
                "analysis_signature": "4" * 64,
                "approval_context_hash": "5" * 64,
                "expires_at": "2026-10-03T00:00:00+00:00",
            },
        )

    context = ContextEnvelope(
        company_id=uuid4(),
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        actor_id=uuid4(),
        actor_role="operations_planner",
        modules=("dispatch",),
        request_hash="1" * 64,
        analysis_signature="2" * 64,
    )
    invocation = ToolInvocation(
        run_id=uuid4(),
        graph_name="dispatch",
        module="dispatch",
        stage_id="dispatch.execute",
        tool_name="create_approval_preview",
        context=context,
        checkpoint_revision=0,
    )
    adapter = RegistryGraphToolInvoker(
        AgentToolRegistry(
            FakeServicePort({"create_approval_preview": handler})  # type: ignore[arg-type]
        ),
        payload_factory=lambda _: {
            "target_type": "dispatch_recommendation",
            "target_id": target_id,
            "payload_hash": "3" * 64,
        },
    )

    result = await adapter.invoke(invocation)

    assert result.status == "approval_required"
    assert result.pending_interrupt is not None
    assert result.pending_interrupt.kind == "approval"
    assert result.pending_interrupt.context_hash == context.analysis_signature
    assert result.pending_interrupt.approval_context_hash == "5" * 64


@pytest.mark.asyncio
async def test_budget_guard_failure_prevents_service_dispatch() -> None:
    handler_called = False

    async def handler(*, context, arguments):
        nonlocal handler_called
        handler_called = True
        return AgentToolResult(status="success")

    class BudgetStop(RuntimeError):
        pass

    def exhausted_guard(*_):
        raise BudgetStop("tool budget exhausted")

    service = FakeServicePort({"resolve_context": handler})
    with pytest.raises(BudgetStop, match="budget exhausted"):
        await AgentToolRegistry(service).invoke(
            "resolve_context",
            context=_context(),
            payload={},
            before_dispatch=exhausted_guard,
        )

    assert handler_called is False
    assert service.lookups == []


def test_result_model_rejects_unsafe_or_non_json_service_data() -> None:
    for unsafe_data in (
        {"sql": "SELECT secret FROM tenant"},
        {"provider": {"api-key": "secret"}},
        {"approval": {"decision": "approve"}},
        {"session": object()},
    ):
        with pytest.raises(ValidationError):
            AgentToolResult(status="success", data=unsafe_data)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_registry_wraps_unsafe_handler_result_without_leaking_it() -> None:
    async def unsafe_handler(*, context, arguments):
        return {"status": "success", "data": {"credentials": "do-not-leak"}}

    service = FakeServicePort(
        {"resolve_context": cast(AgentToolHandler, unsafe_handler)}
    )
    with pytest.raises(AgentToolResultError) as caught:
        await AgentToolRegistry(cast(AgentToolServicePort, service)).invoke(
            "resolve_context",
            context=_context(),
            payload={},
        )

    assert "do-not-leak" not in str(caught.value)


@pytest.mark.asyncio
async def test_not_implemented_handler_is_typed_unsupported_not_success() -> None:
    async def not_implemented(*, context, arguments):
        raise NotImplementedError

    service = FakeServicePort(
        {"sync_grid_forecast": cast(AgentToolHandler, not_implemented)}
    )
    result = await AgentToolRegistry(cast(AgentToolServicePort, service)).invoke(
        "sync_grid_forecast",
        context=_context(),
        payload={"forecast_start": "2026-10-01T00:00:00Z"},
    )

    assert result.status == "unsupported"
    assert result.code == "tool_handler_unavailable"


def test_context_snapshot_is_frozen_and_does_not_alias_request_lists() -> None:
    envelope = _context()
    snapshot = AgentToolContext.from_envelope(envelope)

    with pytest.raises(ValidationError, match="frozen"):
        snapshot.company_id = cast(UUID, uuid4())

    envelope.material_scope.append("mutated after snapshot")
    assert snapshot.material_scope == ("recycled aluminium",)


def test_frozen_context_snapshot_preserves_all_domain_entity_references() -> None:
    identifiers = {name: uuid4() for name in (
        "measurement",
        "current_product",
        "activity",
        "standard",
        "draft",
        "requirement",
        "supplier_product",
        "procurement_scenario",
        "flexible_load",
        "dispatch_scenario",
        "forecast",
        "policy",
        "method",
    )}
    envelope = _context().model_copy(
        update={
            "carbon_measurement_id": identifiers["measurement"],
            "current_product_id": identifiers["current_product"],
            "activity_record_ids": [identifiers["activity"]],
            "standard_id": identifiers["standard"],
            "disclosure_draft_id": identifiers["draft"],
            "requirement_ids": [identifiers["requirement"]],
            "supplier_product_ids": [identifiers["supplier_product"]],
            "procurement_scenario_id": identifiers["procurement_scenario"],
            "flexible_load_id": identifiers["flexible_load"],
            "dispatch_scenario_id": identifiers["dispatch_scenario"],
            "forecast_id": identifiers["forecast"],
            "policy_definition_id": identifiers["policy"],
            "method_definition_id": identifiers["method"],
        }
    )

    snapshot = AgentToolContext.from_envelope(envelope)
    entities = {(item.entity_type, item.entity_id) for item in snapshot.entities}

    assert entities == {
        ("carbon_measurement", identifiers["measurement"]),
        ("current_product", identifiers["current_product"]),
        ("activity_record", identifiers["activity"]),
        ("standard", identifiers["standard"]),
        ("disclosure_draft", identifiers["draft"]),
        ("disclosure_requirement", identifiers["requirement"]),
        ("supplier_product", identifiers["supplier_product"]),
        ("procurement_scenario", identifiers["procurement_scenario"]),
        ("flexible_load", identifiers["flexible_load"]),
        ("dispatch_scenario", identifiers["dispatch_scenario"]),
        ("grid_forecast", identifiers["forecast"]),
        ("policy_definition", identifiers["policy"]),
    }
    assert snapshot.method_definition_ids == (identifiers["method"],)


def test_tool_payload_uses_prior_assurance_mapping_and_dispatch_specific_method() -> None:
    standard_id = uuid4()
    draft_id = uuid4()
    requirement_id = uuid4()
    scenario_id = uuid4()
    procurement_scenario_id = uuid4()
    forecast_id = uuid4()
    dispatch_method_id = uuid4()
    context = ContextEnvelope(
        company_id=uuid4(),
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        actor_id=uuid4(),
        actor_role="sustainability_analyst",
        modules=("assurance", "dispatch"),
        entities=(
            ContextEntity(entity_type="dispatch_scenario", entity_id=scenario_id),
        ),
        method_definition_ids=(uuid4(),),
        request_hash="1" * 64,
        analysis_signature="2" * 64,
    )
    assurance_invocation = ToolInvocation(
        run_id=uuid4(),
        graph_name="assurance",
        module="assurance",
        stage_id="assurance.execute",
        tool_name="decompose_claim",
        context=context,
        prior_outputs=(
            ToolOutput(key="standard_id", value=standard_id),
            ToolOutput(key="disclosure_draft_id", value=draft_id),
            ToolOutput(key="requirement_ids", value=(requirement_id,)),
        ),
        checkpoint_revision=0,
    )
    dispatch_invocation = ToolInvocation(
        run_id=uuid4(),
        graph_name="dispatch",
        module="dispatch",
        stage_id="dispatch.execute",
        tool_name="optimize_dispatch_window",
        context=context,
        prior_outputs=(
            ToolOutput(key="scenario_id", value=procurement_scenario_id),
            ToolOutput(key="forecast_id", value=forecast_id),
            ToolOutput(
                key="dispatch_method_definition_id",
                value=dispatch_method_id,
            ),
        ),
        checkpoint_revision=0,
    )

    assurance_payload = default_tool_payload(assurance_invocation)
    dispatch_payload = default_tool_payload(dispatch_invocation)

    assert assurance_payload["disclosure_draft_id"] == draft_id
    assert assurance_payload["requirement_id"] == requirement_id
    assert dispatch_payload["scenario_id"] == scenario_id
    assert dispatch_payload["forecast_id"] == forecast_id
    assert dispatch_payload["method_definition_id"] == dispatch_method_id


def test_registry_definition_mapping_is_read_only() -> None:
    definitions = AgentToolRegistry().definitions

    with pytest.raises(TypeError):
        cast(dict[str, object], definitions)["execute_sql"] = object()
