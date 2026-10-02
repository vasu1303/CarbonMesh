"""Create domain artifacts from explicitly frozen inputs at existing tool boundaries.

Arithmetic, quality, feasibility, citations and approval policy stay in domain
services. Prepared-record execution remains delegated to the established ports.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.modules.agents.fresh_contracts import FreshMeasurementInput
from app.modules.agents.tool_ports import CarbonMeshAgentToolServicePort
from app.modules.agents.tools import (
    AgentToolContext,
    AgentToolResult,
    CalculateEmissionsInput,
    LoadSupplierCandidatesInput,
    MapStandardRequirementInput,
    OptimizeDispatchWindowInput,
    SyncGridHistoryInput,
)
from app.modules.assurance.errors import AssuranceError
from app.modules.assurance.schemas import DisclosureDraftCreateRequest
from app.modules.dispatch.errors import DispatchError
from app.modules.dispatch.schemas import CreateDispatchScenarioRequest
from app.modules.measurement.schemas import MeasurementCalculateRequest
from app.modules.measurement.service import MeasurementServiceError
from app.modules.procurement.errors import ProcurementError
from app.modules.procurement.schemas import CreateScenarioRequest, MaterialConstraints


class FreshAgentToolServicePort(CarbonMeshAgentToolServicePort):
    async def _resolve_context(self, *, context, arguments):
        if context.fresh_inputs is None:
            return await super()._resolve_context(context=context, arguments=arguments)
        _, failure = self._fresh_measurement_scope(context)
        if failure is not None:
            return failure
        return AgentToolResult(status="success", data={
            "company_id": str(context.company_id),
            "site_id": str(context.site_id),
            "reporting_period_id": str(context.reporting_period_id),
            "analysis_signature": context.analysis_signature,
        }, rows=1)

    async def _sync_grid_history(self, *, context, arguments):
        fresh = context.fresh_inputs
        if fresh is None:
            return await super()._sync_grid_history(context=context, arguments=arguments)
        if not isinstance(arguments, SyncGridHistoryInput):
            return self._invalid_arguments("sync_grid_history")
        # Each provider command retains its own immutable snapshot and cache key.
        # Bounds come from the signed request, not from a model or prior output.
        if arguments.start != fresh.history_start or arguments.end != fresh.history_end:
            return AgentToolResult(status="policy_blocked", code="history_scope_mismatch")
        cursor = arguments.start
        total = 0
        all_cached = True
        while cursor < arguments.end:
            end = min(cursor + timedelta(hours=240), arguments.end)
            chunk = arguments.model_copy(update={"start": cursor, "end": end,
                                                 "fixture_variant": fresh.history_fixture_variant})
            result = await super()._sync_grid_history(context=context, arguments=chunk)
            if result.status != "success":
                return result
            total += result.rows
            all_cached = all_cached and result.cache.hit is True
            cursor = end
        return AgentToolResult(status="success", rows=total, cache={"hit": all_cached},
                               data={"period_start": arguments.start.isoformat(),
                                     "period_end": arguments.end.isoformat()})

    async def _calculate_emissions(self, *, context, arguments):
        fresh = context.fresh_inputs
        if fresh is None:
            return await super()._calculate_emissions(context=context, arguments=arguments)
        if not isinstance(arguments, CalculateEmissionsInput):
            return self._invalid_arguments("calculate_emissions")
        if self._measurement is None or not fresh.measurements:
            return AgentToolResult(status="no_data", code="source_measurement_inputs_required")
        sources, failure = self._fresh_measurement_scope(context)
        if failure is not None:
            return failure
        facts = []
        data = {}
        rows = 0
        for source in sources:
            is_scope2 = source.output_metric_key == "emissions.scope2.location_based"
            try:
                measurement = await self._measurement.calculate(MeasurementCalculateRequest(
                    company_id=context.company_id, site_id=context.site_id,
                    reporting_period_id=context.reporting_period_id,
                    actor_id=context.actor_id, agent_run_id=context.run_id,
                    material_code=source.material_code,
                    output_metric_key=source.output_metric_key,
                    activity_record_ids=list(source.activity_record_ids) or None,
                    geography=source.geography, grid_zone=source.grid_zone,
                    grid_method_version=source.grid_method_version,
                ))
            except MeasurementServiceError as error:
                return self._measurement_failure(error)
            failure = self._measurement_scope_failure(context, measurement)
            if failure is not None:
                return failure
            fact, failure = self._measurement_fact(measurement)
            if failure is not None:
                return failure
            facts.append(fact)
            key = "scope2_measurement_id" if is_scope2 else "material_measurement_id"
            data[key] = str(measurement.id)
            rows += len(measurement.inputs)
        data["fact_ids"] = [str(fact.fact_id) for fact in facts]
        data["ledger_event_ids"] = [str(fact.ledger_event_id) for fact in facts]
        return AgentToolResult(status="success", data=data, facts=tuple(facts), rows=rows)

    @staticmethod
    def _fresh_measurement_scope(
        context: AgentToolContext,
    ) -> tuple[tuple[FreshMeasurementInput, ...], AgentToolResult | None]:
        """Resolve the complete source set before any domain/provider mutation.

        Fresh calculations select versioned methods from each output metric.
        A prepared-record method/measurement selector cannot simultaneously
        redefine that source-building operation and is rejected explicitly.
        """
        sources = context.fresh_inputs.measurements if context.fresh_inputs else ()
        if not sources:
            return sources, None
        if context.method_definition_ids or context.method_definition_id is not None:
            return (), AgentToolResult(
                status="policy_blocked", code="fresh_measurement_method_selector_conflict",
                message="Fresh calculations use each metric's versioned method; a prepared method selector cannot also be supplied.",
            )
        if context.carbon_measurement_id is not None:
            return (), AgentToolResult(status="policy_blocked", code="fresh_measurement_target_conflict",
                                       message="Choose source calculations or a prepared measurement, not both.")
        outer_ids = {entity.entity_id for entity in context.entities if entity.entity_type == "activity_record"}
        if outer_ids:
            if len(sources) == 1 and not sources[0].activity_record_ids:
                sources = (sources[0].model_copy(update={"activity_record_ids": tuple(sorted(outer_ids, key=str))}),)
            if (any(not source.activity_record_ids for source in sources)
                    or {item for source in sources for item in source.activity_record_ids} != outer_ids):
                return (), AgentToolResult(status="policy_blocked", code="fresh_measurement_activity_scope_mismatch",
                                           message="Fresh source activity IDs must exactly match the frozen outer activity scope.")
        # Validate every source before calculating the first one. An invalid
        # second source cannot leave an unexpected first calculation behind.
        for source in sources:
            if source.output_metric_key not in context.metric_keys:
                return (), AgentToolResult(status="policy_blocked", code="measurement_metric_outside_scope")
            if (source.output_metric_key != "emissions.scope2.location_based"
                    and source.material_code not in context.material_scope):
                return (), AgentToolResult(status="policy_blocked", code="measurement_material_outside_scope")
        return sources, None

    async def _map_standard_requirement(self, *, context, arguments):
        result = await super()._map_standard_requirement(context=context, arguments=arguments)
        if context.fresh_inputs is None or result.status != "success" or self._assurance_draft_id(context):
            return result
        if not isinstance(arguments, MapStandardRequirementInput) or self._assurance is None:
            return self._invalid_arguments("map_standard_requirement")
        measurement_id = self._measurement_id(context)
        if measurement_id is None:
            return self._clarification(code="assurance_measurement_required",
                                       field="context.carbon_measurement_id",
                                       message="A verified Scope 2 measurement is required.")
        try:
            draft = await self._assurance.create_draft(DisclosureDraftCreateRequest(
                company_id=context.company_id, site_id=context.site_id,
                reporting_period_id=context.reporting_period_id,
                standard_id=arguments.standard_id, measurement_id=measurement_id,
                requirement_ids=arguments.requirement_ids,
                requested_by=context.actor_id, agent_run_id=context.run_id,
                idempotency_key=f"agent:{context.analysis_signature}:draft",
            ))
        except AssuranceError as error:
            return self._assurance_failure(error)
        return result.model_copy(update={"data": {
            **result.data, "disclosure_draft_id": str(draft.id),
        }})

    async def _load_supplier_candidates(self, *, context, arguments):
        fresh = context.fresh_inputs
        if fresh is None or self._entity_id(context, "procurement_scenario") is not None:
            return await super()._load_supplier_candidates(context=context, arguments=arguments)
        if not isinstance(arguments, LoadSupplierCandidatesInput) or self._procurement is None:
            return self._invalid_arguments("load_supplier_candidates")
        measurement_id = self._measurement_id(context)
        if measurement_id is None:
            return self._clarification(code="procurement_measurement_required",
                                       field="context.carbon_measurement_id",
                                       message="A verified purchased-material measurement is required.")
        if context.supplier_product_ids:
            return AgentToolResult(status="unsupported", code="supplier_product_scope_unsupported")
        try:
            scenario = await self._procurement.create_scenario(CreateScenarioRequest(
                company_id=context.company_id, site_id=context.site_id,
                reporting_period_id=context.reporting_period_id,
                requested_by=context.actor_id, agent_run_id=context.run_id,
                current_product_id=context.current_product_id,
                carbon_measurement_id=measurement_id,
                method_definition_id=fresh.procurement_method_id,
                quantity=fresh.procurement_quantity, quantity_unit=fresh.procurement_quantity_unit,
                max_cost_increase_pct=context.constraints.get("max_cost_increase_pct"),
                max_lead_time_days=context.constraints.get("max_lead_time_days"),
                minimum_circularity_score=context.constraints.get("minimum_circularity_score"),
                material_constraints=MaterialConstraints(allowed_material_codes=list(context.material_scope)),
                idempotency_key=f"agent:{context.analysis_signature}:procurement",
            ))
        except ProcurementError as error:
            return self._procurement_failure(error)
        derived = {**context.derived, "procurement_scenario_id": str(scenario.id)}
        return await super()._load_supplier_candidates(
            context=context.model_copy(update={"derived": derived}),
            arguments=arguments.model_copy(update={"scenario_id": scenario.id}),
        )

    async def _optimize_dispatch_window(self, *, context, arguments):
        fresh = context.fresh_inputs
        if fresh is None or self._entity_id(context, "dispatch_scenario") is not None:
            return await super()._optimize_dispatch_window(context=context, arguments=arguments)
        if not isinstance(arguments, OptimizeDispatchWindowInput) or self._dispatch is None:
            return self._invalid_arguments("optimize_dispatch_window")
        try:
            scenario = await self._dispatch.create_scenario(CreateDispatchScenarioRequest(
                company_id=context.company_id, site_id=context.site_id,
                flexible_load_id=self._entity_id(context, "flexible_load"),
                method_definition_id=fresh.dispatch_method_id,
                policy_definition_id=self._entity_id(context, "policy_definition"),
                forecast_source_document_id=arguments.forecast_id,
                requested_by=context.actor_id, agent_run_id=context.run_id,
                window_start=datetime.fromisoformat(str(context.constraints["dispatch.window_start"])),
                window_end=datetime.fromisoformat(str(context.constraints["dispatch.window_end"])),
                baseline_start=fresh.dispatch_baseline_start,
                maximum_delay_minutes=int(context.constraints["dispatch.max_delay_minutes"]),
                available_capacity_kw=(Decimal(str(context.constraints["dispatch.maximum_power_kw"]))
                                       if "dispatch.maximum_power_kw" in context.constraints else None),
                idempotency_key=f"agent:{context.analysis_signature}:dispatch",
            ))
        except DispatchError as error:
            return self._dispatch_failure(error)
        derived = {**context.derived, "dispatch_scenario_id": str(scenario.id)}
        return await super()._optimize_dispatch_window(
            context=context.model_copy(update={"derived": derived}),
            arguments=arguments.model_copy(update={"scenario_id": scenario.id,
                                                   "method_definition_id": fresh.dispatch_method_id}),
        )

    @staticmethod
    def _entity_id(context: AgentToolContext, entity_type: str) -> UUID | None:
        frozen = CarbonMeshAgentToolServicePort._entity_id(context, entity_type)
        if frozen is not None or context.fresh_inputs is None:
            return frozen
        value = context.derived.get(f"{entity_type}_id")
        return UUID(str(value)) if value is not None else None

    @classmethod
    def _measurement_id(cls, context: AgentToolContext) -> UUID | None:
        if context.fresh_inputs is not None:
            key = "scope2_measurement_id" if context.current_module == "assurance" else "material_measurement_id"
            value = context.derived.get(key)
            if value is not None:
                return UUID(str(value))
        return super()._measurement_id(context)
