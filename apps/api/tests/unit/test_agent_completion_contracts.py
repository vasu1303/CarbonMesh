from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agents.fresh_contracts import FreshRunInputs
from app.modules.agents.fresh_tools import FreshAgentToolServicePort
from app.modules.agents.graph_contracts import ContextEntity
from app.modules.agents.ledger_replay import DomainLedgerWriteReplay, LedgerWriteReplayError
from app.modules.agents.tool_ports import CarbonMeshAgentToolServicePort
from app.modules.agents.tools import (
    AgentToolContext,
    AgentToolContextEntity,
    AgentToolRegistry,
    CalculateEmissionsInput,
    WriteLedgerEventInput,
)
from tests.unit.test_agent_tool_ports import _context, _measurement, _MeasurementService


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["activity", "method", "ambiguous_sources", "later_material"])
async def test_fresh_measurement_rejects_conflicting_frozen_selectors_before_writes(conflict):
    outer_id = uuid4()
    class NoWrites:
        async def calculate(self, request):
            pytest.fail("a conflicting frozen source must never reach domain calculation")
    context = AgentToolContext(
        company_id=uuid4(), actor_id=uuid4(), actor_role="sustainability_analyst",
        site_id=uuid4(), reporting_period_id=uuid4(), modules=("measurement",),
        request_hash="a" * 64, analysis_signature="b" * 64,
        metric_keys=("emissions.scope3.category1", "emissions.scope2.location_based"),
        material_scope=("RECYCLED-ALUMINIUM",),
        entities=(AgentToolContextEntity(entity_type="activity_record", entity_id=outer_id),),
        fresh_inputs=FreshRunInputs(measurements=({"material_code": "RECYCLED-ALUMINIUM",
            "output_metric_key": "emissions.scope3.category1", "activity_record_ids": (outer_id,)},)),
    )
    source = context.fresh_inputs.measurements[0]
    if conflict == "activity":
        context = context.model_copy(update={"fresh_inputs": FreshRunInputs(measurements=(
            source.model_copy(update={"activity_record_ids": (uuid4(),)}),))})
    elif conflict == "method":
        context = context.model_copy(update={"method_definition_ids": (uuid4(),)})
    elif conflict == "ambiguous_sources":
        context = context.model_copy(update={"fresh_inputs": FreshRunInputs(measurements=(source,
            {"material_code": "ELECTRICITY", "output_metric_key": "emissions.scope2.location_based"}))})
    else:
        context = context.model_copy(update={"entities": (), "fresh_inputs": FreshRunInputs(measurements=(
            {"material_code": "ELECTRICITY", "output_metric_key": "emissions.scope2.location_based"},
            source.model_copy(update={"material_code": "OUTSIDE-SCOPE"}),))})
    result = await FreshAgentToolServicePort(measurement_service=NoWrites())._calculate_emissions(
        context=context, arguments=CalculateEmissionsInput())
    assert result.status == "policy_blocked"


@pytest.mark.asyncio
async def test_empty_fresh_source_inherits_explicit_outer_activity_scope():
    outer_id, company, site, period = (uuid4() for _ in range(4))
    measurement = _measurement(company_id=company, site_id=site, period_id=period,
                               measurement_id=uuid4(), activity_id=outer_id, method_id=uuid4(), factor_id=uuid4())
    calls = []
    class Service:
        async def calculate(self, request):
            calls.append(request)
            return measurement
    context = AgentToolContext(
        company_id=company, actor_id=uuid4(), actor_role="sustainability_analyst",
        site_id=site, reporting_period_id=period, modules=("measurement",),
        request_hash="a" * 64, analysis_signature="b" * 64,
        metric_keys=("emissions.scope3.category1",), material_scope=("RECYCLED-ALUMINIUM",),
        entities=(AgentToolContextEntity(entity_type="activity_record", entity_id=outer_id),),
        fresh_inputs=FreshRunInputs(measurements=({"material_code": "RECYCLED-ALUMINIUM",
            "output_metric_key": "emissions.scope3.category1"},)),
    )
    result = await FreshAgentToolServicePort(measurement_service=Service())._calculate_emissions(
        context=context, arguments=CalculateEmissionsInput())
    assert result.status == "success"
    assert len(calls) == 1
    assert calls[0].activity_record_ids == [outer_id]


@pytest.mark.parametrize("module", ["assurance", "procurement"])
def test_fresh_followup_plan_revalidates_sources_before_dependent_artifact_creation(module):
    from app.modules.agents.planning import PlannerSelection, build_execution_plan

    plan = build_execution_plan(PlannerSelection(disposition="execute", modules=(module,),
                                                 reason_code="followup_request"),
                                fresh_inputs=FreshRunInputs(measurements=({"material_code": "RECYCLED-ALUMINIUM",
                                    "output_metric_key": "emissions.scope3.category1"},)))
    assert plan.modules == ("measurement", module)
    assert plan.stage_for(module).depends_on == ("measurement",)
    assert "calculate_emissions" in plan.stage_for("measurement").tool_names
    assert plan.expected_model_calls == 1


@pytest.mark.asyncio
async def test_followup_runtime_freezes_expanded_plan_and_hands_measurement_to_procurement():
    from datetime import UTC, datetime, timedelta

    from app.modules.agents.planning import PlannerSelection
    from app.modules.agents.runtime import GraphAgentRunService
    from app.modules.agents.schemas import AgentContextRequest, AgentQueryRequest
    from app.modules.agents.tools import AgentToolFact, AgentToolResult
    from tests.unit.test_agent_runtime_evaluations import FakeRuntimeRepository, ScriptedModel

    repository = FakeRuntimeRepository()
    measurement, event, scenario, score, target, approval = (uuid4() for _ in range(6))
    calls = []
    class Port:
        def handler_for(self, tool):
            async def handler(*, context, arguments):
                calls.append((tool, context))
                assert context.modules == ("measurement", "procurement")
                assert context.constraints["max_cost_increase_pct"] == "2"
                if tool == "calculate_emissions":
                    return AgentToolResult(status="success", data={"material_measurement_id": str(measurement)},
                                           facts=(AgentToolFact(fact_id=measurement, metric_key="emissions.scope3.category1",
                                                               ledger_event_id=event, display_value="86000 kgCO2e"),))
                if tool == "load_supplier_candidates":
                    assert context.derived["material_measurement_id"] == str(measurement)
                    return AgentToolResult(status="success", data={"scenario_id": str(scenario),
                                                                   "selected_supplier_score_id": str(score)})
                if tool == "build_procurement_recommendation":
                    assert arguments.scenario_id == scenario
                    return AgentToolResult(status="success", data={
                        "approval_target_type": "procurement_recommendation", "approval_target_id": str(target),
                        "payload_hash": "c" * 64})
                if tool == "create_approval_preview":
                    return AgentToolResult(status="approval_required", data={
                        "approval_id": str(approval), "target_type": "procurement_recommendation", "target_id": str(target),
                        "preview_hash": "c" * 64, "analysis_signature": context.analysis_signature,
                        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()})
                return AgentToolResult(status="success")
            return handler
    model = ScriptedModel([PlannerSelection(disposition="execute", modules=("procurement",),
                                           reason_code="tighter_procurement_constraint").model_dump_json()])
    service = GraphAgentRunService(repository, model_factory=lambda: model,
                                   tool_registry_factory=lambda: AgentToolRegistry(Port()))
    original = AgentQueryRequest(query="Review procurement", context=AgentContextRequest(
        company_id=uuid4(), actor_id=uuid4(), site_id=uuid4(), reporting_period_id=uuid4(),
        current_product_id=uuid4(), material_scope=["RECYCLED-ALUMINIUM"],
        constraints={"max_cost_increase_pct": "5", "max_lead_time_days": 30, "minimum_circularity_score": "0"},
        fresh_inputs=FreshRunInputs(procurement_quantity="10000", procurement_method_id=uuid4(),
                                   measurements=({"material_code": "RECYCLED-ALUMINIUM",
                                                  "output_metric_key": "emissions.scope3.category1"},))))
    prior = await service.start(original, trace_id="prior")
    repository.runs[prior.run_id].terminal_state = "success"
    request = AgentQueryRequest(query="Use the same plant with a tighter cost constraint", previous_run_id=prior.run_id,
                                context=AgentContextRequest(company_id=original.context.company_id,
                                                            actor_id=original.context.actor_id,
                                                            constraints={"max_cost_increase_pct": "2"}))
    accepted = await service.start(request, trace_id="followup")
    await service.execute(request=request, run_id=accepted.run_id)
    run = await service.get(company_id=original.context.company_id, run_id=accepted.run_id)
    assert run.terminal_state == "approval_required", run.error_code
    assert run.workflow == "cross_module"
    assert run.plan["profile"] == "golden"
    assert run.telemetry.graph_context["modules"] == ["measurement", "procurement"]
    assert run.telemetry.graph_state["budget"]["profile"] == "golden"
    assert run.context.analysis_signature != repository.runs[prior.run_id].context_hash
    assert [tool for tool, _ in calls] == ["resolve_context", "calculate_emissions", "load_supplier_candidates",
                                          "build_procurement_recommendation", "create_approval_preview"]
    assert run.telemetry.tool_calls == 5
    assert run.telemetry.model_calls == 1
    assert run.pending_interrupt.approval_id == approval


@pytest.mark.asyncio
async def test_hourly_scope2_replays_exact_grid_intervals_and_kwh_dimension():
    company, site, period, actor, measurement_id, method = (uuid4() for _ in range(6))
    measurement = _measurement(company_id=company, site_id=site, period_id=period,
                               measurement_id=measurement_id, activity_id=uuid4(),
                               method_id=method, factor_id=uuid4())
    measurement.metric_key = "emissions.scope2.location_based"
    measurement.inputs = [SimpleNamespace(activity_record_id=uuid4()) for _ in range(120)]
    measurement.calculations = [SimpleNamespace(
        activity_record_id=item.activity_record_id, emission_factor_id=None,
        grid_intensity_point_id=uuid4(), output_hash="a" * 64,
    ) for item in measurement.inputs]
    context = _context(company_id=company, site_id=site, period_id=period, actor_id=actor,
                       modules=("measurement",), method_ids=(method,),
                       entities=(ContextEntity(entity_type="carbon_measurement", entity_id=measurement_id),))
    context = context.model_copy(update={"metric_keys": (measurement.metric_key,)})
    registry = AgentToolRegistry(CarbonMeshAgentToolServicePort(
        measurement_service=_MeasurementService(company, measurement)))
    normalized = await registry.invoke("normalize_unit", context=context, payload={"canonical_unit": "kWh"})
    assert normalized.status == "success"
    assert normalized.data["activity_record_ids"] == []
    invalid_unit = await registry.invoke("normalize_unit", context=context, payload={"canonical_unit": "kg"})
    assert invalid_unit.status == "validation_failed"
    selected = await registry.invoke("select_emission_factor", context=context, payload={"metric_key": measurement.metric_key})
    assert selected.status == "success"
    assert selected.data["emission_factor_id"] is None
    arguments = {"method_definition_id": method, "factor_set_hash": selected.data["factor_set_hash"]}
    calculated = await registry.invoke("calculate_emissions", context=context, payload=arguments)
    assert calculated.status == "success"
    assert calculated.facts[0].fact_id == measurement.facts.fact_id
    measurement.calculations[-1].grid_intensity_point_id = uuid4()
    stale = await registry.invoke("calculate_emissions", context=context, payload=arguments)
    assert stale.code == "measurement_factor_mismatch"


@pytest.mark.asyncio
async def test_ledger_tool_replays_exact_current_run_proof_and_rejects_forged_links():
    event, source, subject, evidence, method, run_id = (uuid4() for _ in range(6))
    class Repository:
        async def domain_ledger_write_proof(self, **scope):
            if scope["run_id"] != run_id or scope["payload_hash"] != "a" * 64:
                return None
            return event, "a" * 64, {source}, {subject}, {evidence}, method
    replay = DomainLedgerWriteReplay(Repository())
    context = AgentToolContext(company_id=uuid4(), actor_id=uuid4(), actor_role="analyst",
                               site_id=uuid4(), reporting_period_id=uuid4(),
                               request_hash="b" * 64, analysis_signature="c" * 64, run_id=run_id)
    arguments = WriteLedgerEventInput(event_type="measurement.verified", subject_type="carbon_measurement",
                                      subject_id=subject, payload_hash="a" * 64,
                                      source_event_ids=(source,), fact_ids=(subject,),
                                      evidence_item_ids=(evidence,), method_definition_id=method)
    assert await replay(context, arguments) == await replay(context, arguments) == (event, "a" * 64)
    for change in ({"source_event_ids": (uuid4(),)}, {"fact_ids": (uuid4(),)},
                   {"evidence_item_ids": (uuid4(),)}, {"method_definition_id": uuid4()},
                   {"payload_hash": "d" * 64}, {"event_type": "approval.approved"}):
        with pytest.raises(LedgerWriteReplayError):
            await replay(context, arguments.model_copy(update=change))
    with pytest.raises(LedgerWriteReplayError):
        await replay(context.model_copy(update={"run_id": uuid4()}), arguments)


@pytest.mark.asyncio
async def test_footprint_constants_stay_frozen_across_execution_segments(monkeypatch):
    from time import perf_counter

    from app.core.config import Settings
    from app.core.observability import (
        begin_external_usage,
        record_external_cache_hit,
        record_external_call,
    )
    from app.modules.agents import runtime
    from app.modules.agents.budget import new_budget
    from app.modules.agents.schemas import AgentContextRequest, AgentQueryRequest
    from app.modules.agents.step_recorder import AgentStepRecorder
    from tests.unit.test_agent_runtime_evaluations import FakeRuntimeRepository

    repository = FakeRuntimeRepository()
    service = runtime.GraphAgentRunService(repository)
    accepted = await service.start(AgentQueryRequest(query="Measure inputs", context=AgentContextRequest(
        company_id=uuid4(), actor_id=uuid4())), trace_id="proxy-test")
    run = repository.runs[accepted.run_id]
    recorder = AgentStepRecorder(repository, company_id=run.company_id, run_id=run.id)
    for constant, tokens in (("1", 1000), ("100", 2000)):
        monkeypatch.setattr(runtime, "get_settings", lambda constant=constant: Settings(_env_file=None,
            ai_energy_wh_per_1k_tokens=constant, ai_grid_intensity_gco2e_per_kwh="500"))
        run.input_tokens = tokens
        begin_external_usage()
        record_external_call("grid")
        record_external_cache_hit("grid")
        await service._stop(run=run, recorder=recorder, terminal_state="no_data", code="no_data",
                            message="Missing source", budget=new_budget("single"), started_clock=perf_counter())
    assert run.estimated_energy_wh == Decimal(2)
    assert run.estimated_co2e_g == Decimal(1)
    assert run.telemetry["sustainability_assumptions"]["energy_wh_per_1k_tokens"] == "1"
    assert run.api_calls == 2
    assert run.telemetry["external_cache_hits"] == 2
