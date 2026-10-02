"""Production adapters from typed agent tools to deterministic domain services.

Every frozen tool ID has a production handler.  Handlers compose existing
application-service operations and fail closed when the landed service contract
cannot represent the requested operation; business calculations remain in the
owning domain services.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.modules.agents.ledger_replay import LedgerWriteReplayError
from app.modules.agents.schemas import AgentContextRequest
from app.modules.agents.tools import (
    AgentToolContext,
    AgentToolFact,
    AgentToolHandler,
    AgentToolName,
    AgentToolResult,
    AgentToolStatus,
    BindClaimFactsInput,
    BuildProcurementRecommendationInput,
    CalculateConfidenceInput,
    CalculateDispatchImpactInput,
    CalculateEmissionsInput,
    CalculateProcurementImpactInput,
    CreateApprovalPreviewInput,
    DecomposeClaimInput,
    DetectEvidenceGapsInput,
    LoadSupplierCandidatesInput,
    MapStandardRequirementInput,
    NormalizeUnitInput,
    OptimizeDispatchWindowInput,
    ResolveEntityInput,
    RetrieveEvidenceInput,
    RetrieveLedgerFactsInput,
    ScoreSupplierInput,
    SelectEmissionFactorInput,
    StrictToolInput,
    SyncGridForecastInput,
    SyncGridHistoryInput,
    ValidateActivityInput,
    ValidateCitationsInput,
    WriteLedgerEventInput,
)
from app.modules.agents.workflow import (
    CARBON_METRIC_KEY,
    PROCUREMENT_METRIC_KEYS,
    AgentWorkflowExecutor,
    ResolvedWorkflowInputs,
    WorkflowStop,
)
from app.modules.assurance.errors import AssuranceError
from app.modules.assurance.schemas import DisclosureDraftValidateRequest, DisclosureDraftView
from app.modules.assurance.service import AssuranceService
from app.modules.dispatch.errors import DispatchError
from app.modules.dispatch.schemas import (
    DispatchScenarioView,
    ForecastSyncRequest,
    OptimizeScenarioRequest,
)
from app.modules.dispatch.service import DispatchService
from app.modules.integrations.electricity_maps import ElectricityMapsProvider
from app.modules.integrations.schemas import GridIntensitySyncResult
from app.modules.integrations.service import IntegrationServiceError
from app.modules.measurement.schemas import MeasurementDetail
from app.modules.measurement.service import MeasurementService, MeasurementServiceError
from app.modules.procurement.errors import ProcurementError
from app.modules.procurement.schemas import ProcurementScenarioResult
from app.modules.procurement.service import ProcurementService

GridHistorySync = Callable[
    [AgentToolContext, SyncGridHistoryInput],
    Awaitable[GridIntensitySyncResult],
]
LedgerEventWrite = Callable[
    [AgentToolContext, WriteLedgerEventInput],
    Awaitable[tuple[UUID, str]],
]


class CarbonMeshAgentToolServicePort:
    """Expose landed services through the frozen, session-free tool protocol.

    The port owns service objects supplied by the API composition root. Graph
    nodes see only ``AgentToolContext`` plus typed arguments; they never receive
    an ``AsyncSession`` or a repository.

    Measurement handlers replay an already verified deterministic measurement.
    Procurement handlers replay an already frozen scenario and its deterministic
    assessment. Creating either domain object is intentionally not hidden behind
    a read/granular tool whose input contract cannot bind the full transaction.
    """

    def __init__(
        self,
        handlers: Mapping[AgentToolName, AgentToolHandler] | None = None,
        *,
        measurement_service: MeasurementService | None = None,
        procurement_service: ProcurementService | None = None,
        assurance_service: AssuranceService | None = None,
        dispatch_service: DispatchService | None = None,
        live_grid_provider: ElectricityMapsProvider | None = None,
        fixture_grid_provider: ElectricityMapsProvider | None = None,
        grid_history_sync: GridHistorySync | None = None,
        ledger_event_write: LedgerEventWrite | None = None,
        workflow_resolver: AgentWorkflowExecutor | None = None,
    ) -> None:
        self._measurement = measurement_service
        self._procurement = procurement_service
        self._assurance = assurance_service
        self._dispatch = dispatch_service
        self._live_grid_provider = live_grid_provider
        self._fixture_grid_provider = fixture_grid_provider
        self._grid_history_sync = grid_history_sync
        self._ledger_event_write = ledger_event_write
        self._workflow_resolver = workflow_resolver
        self._handlers = dict(handlers or {})
        self._handlers.setdefault("resolve_context", self._resolve_context)
        self._handlers.setdefault("resolve_entity", self._resolve_entity)

        # Shared mutating operations are backed only by explicit adapters over
        # the existing ledger/application transaction boundaries.
        self._handlers.setdefault("retrieve_evidence", self._retrieve_evidence)
        self._handlers.setdefault("write_ledger_event", self._write_ledger_event)
        self._handlers.setdefault("create_approval_preview", self._create_approval_preview)

        if measurement_service is not None:
            self._handlers.setdefault("retrieve_ledger_facts", self._retrieve_ledger_facts)
            self._handlers.setdefault("validate_activity", self._validate_activity)
            self._handlers.setdefault("normalize_unit", self._normalize_unit)
            self._handlers.setdefault("select_emission_factor", self._select_emission_factor)
            self._handlers.setdefault("calculate_emissions", self._calculate_emissions)
            self._handlers.setdefault("calculate_confidence", self._calculate_confidence)

        self._handlers.setdefault("sync_grid_history", self._sync_grid_history)

        if assurance_service is not None:
            self._handlers.setdefault(
                "map_standard_requirement", self._map_standard_requirement
            )
            self._handlers.setdefault("decompose_claim", self._decompose_claim)
            self._handlers.setdefault("bind_claim_facts", self._bind_claim_facts)
            self._handlers.setdefault("validate_citations", self._validate_citations)
            self._handlers.setdefault("detect_evidence_gaps", self._detect_evidence_gaps)

        if procurement_service is not None:
            self._handlers.setdefault("load_supplier_candidates", self._load_supplier_candidates)
            self._handlers.setdefault("score_supplier", self._score_supplier)
            self._handlers.setdefault(
                "calculate_procurement_impact",
                self._calculate_procurement_impact,
            )
            self._handlers.setdefault(
                "build_procurement_recommendation",
                self._build_procurement_recommendation,
            )
        if dispatch_service is not None:
            self._handlers.setdefault("sync_grid_forecast", self._sync_grid_forecast)
            self._handlers.setdefault(
                "optimize_dispatch_window", self._optimize_dispatch_window
            )
            self._handlers.setdefault(
                "calculate_dispatch_impact", self._calculate_dispatch_impact
            )

    def handler_for(self, tool_name: AgentToolName) -> AgentToolHandler | None:
        return self._handlers.get(tool_name)

    async def _resolve_entity(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, ResolveEntityInput):
            return self._invalid_arguments("resolve_entity")
        reference = self._normalized_reference(arguments.reference)
        current_ids: dict[str, UUID | None] = {
            "site": context.site_id,
            "reporting_period": context.reporting_period_id,
            "measurement": self._measurement_id(context),
        }
        current_id = current_ids.get(arguments.entity_type)
        if current_id is not None:
            if reference in {"current", "selected", str(current_id).casefold()}:
                return self._resolved_entity(arguments.entity_type, current_id)
            if arguments.entity_type == "measurement" and self._measurement is not None:
                try:
                    requested_id = UUID(arguments.reference)
                except ValueError:
                    requested_id = None
                if requested_id is not None:
                    measurement, failure = await self._measurement_detail(
                        context,
                        measurement_id=requested_id,
                        allow_resolution=False,
                    )
                    if failure is not None:
                        return failure
                    assert measurement is not None
                    return self._resolved_entity("measurement", measurement.id)
            return self._entity_not_resolved(arguments.entity_type)

        if arguments.entity_type == "standard" and self._assurance is not None:
            try:
                standards = await self._assurance.list_standards(
                    company_id=context.company_id,
                    active_only=True,
                    limit=100,
                    offset=0,
                )
            except AssuranceError as error:
                return self._assurance_failure(error)
            matches = [
                item
                for item in standards.items
                if reference
                in {
                    str(item.id).casefold(),
                    self._normalized_reference(item.code),
                    self._normalized_reference(item.name),
                }
            ]
            return self._unique_entity_result("standard", [item.id for item in matches])

        if arguments.entity_type in {"supplier", "supplier_product"}:
            if self._procurement is None:
                return AgentToolResult.unsupported("resolve_entity")
            try:
                products = await self._procurement.list_supplier_products(
                    company_id=context.company_id,
                    supplier_id=None,
                    material_code=None,
                    category=None,
                    active_only=True,
                    search=arguments.reference,
                    limit=100,
                    offset=0,
                )
            except ProcurementError as error:
                return self._procurement_failure(error)
            if arguments.entity_type == "supplier":
                identifiers = {
                    item.supplier_id
                    for item in products.items
                    if reference
                    in {
                        str(item.supplier_id).casefold(),
                        self._normalized_reference(item.supplier_code),
                        self._normalized_reference(item.supplier_name),
                    }
                }
            else:
                identifiers = {
                    item.id
                    for item in products.items
                    if reference
                    in {
                        str(item.id).casefold(),
                        self._normalized_reference(item.product_code),
                        self._normalized_reference(item.name),
                    }
                }
            return self._unique_entity_result(arguments.entity_type, sorted(identifiers, key=str))

        if arguments.entity_type == "flexible_load" and self._dispatch is not None:
            try:
                loads = await self._dispatch.list_loads(
                    company_id=context.company_id,
                    site_id=context.site_id,
                    active_only=True,
                    limit=100,
                    offset=0,
                )
            except DispatchError as error:
                return self._dispatch_failure(error)
            matches = [
                item
                for item in loads.items
                if reference
                in {
                    str(item.id).casefold(),
                    self._normalized_reference(item.code),
                    self._normalized_reference(item.name),
                }
            ]
            return self._unique_entity_result("flexible_load", [item.id for item in matches])

        return self._entity_not_resolved(arguments.entity_type)

    async def _sync_grid_history(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, SyncGridHistoryInput):
            return self._invalid_arguments("sync_grid_history")
        if self._grid_history_sync is None:
            return AgentToolResult.unsupported("sync_grid_history")
        if context.site_id is None:
            return self._clarification(
                code="grid_history_site_required",
                field="context.site_id",
                message="A resolved site is required to synchronize grid history.",
            )
        try:
            result = await self._grid_history_sync(context, arguments)
        except IntegrationServiceError as error:
            return self._integration_failure(error)
        return AgentToolResult(
            status="success",
            data={
                "site_id": str(result.site_id),
                "grid_zone": result.zone,
                "period_start": result.requested_start.isoformat(),
                "period_end": result.requested_end.isoformat(),
                "source_document_id": str(result.source_document_id),
                "data_source_id": str(result.data_source_id),
                "payload_hash": result.response_checksum,
                "grid_source_mode": arguments.source_mode,
            },
            rows=result.received_points,
            cache={"hit": result.inserted_points == 0 and result.existing_points > 0},
        )

    async def _write_ledger_event(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, WriteLedgerEventInput):
            return self._invalid_arguments("write_ledger_event")
        if self._ledger_event_write is None:
            return AgentToolResult.unsupported("write_ledger_event")
        try:
            event_id, event_payload_hash = await self._ledger_event_write(context, arguments)
        except LedgerWriteReplayError:
            return AgentToolResult(status="policy_blocked", code="ledger_write_proof_mismatch",
                                   message="No exact domain-owned ledger event matches this run and its evidence.")
        except SQLAlchemyError:
            return AgentToolResult(
                status="failed",
                code="ledger_write_unavailable",
                message="The immutable ledger event could not be persisted.",
            )
        return AgentToolResult(
            status="success",
            data={
                "ledger_event_id": str(event_id),
                "ledger_event_ids": [str(event_id)],
                "payload_hash": arguments.payload_hash,
                "ledger_payload_hash": event_payload_hash,
            },
            rows=1,
        )

    async def _resolve_context(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        del arguments
        data: dict[str, object] = {
            "company_id": str(context.company_id),
            "site_id": str(context.site_id) if context.site_id else None,
            "reporting_period_id": (
                str(context.reporting_period_id) if context.reporting_period_id else None
            ),
            "analysis_signature": context.analysis_signature,
            "module_count": len(context.modules),
        }

        rows_resolved = 0
        resolved_scoped_scenario = False
        procurement_scenario_id = self._entity_id(context, "procurement_scenario")
        if procurement_scenario_id is not None and self._procurement is not None:
            scenario, failure = await self._scenario(context, procurement_scenario_id)
            if failure is not None:
                return failure
            assert scenario is not None
            data.update(self._scenario_outputs(scenario))
            measurement, failure = await self._measurement_detail(
                context,
                measurement_id=scenario.carbon_measurement_id,
                allow_resolution=False,
            )
            if failure is not None:
                return failure
            assert measurement is not None
            data.update(self._measurement_outputs(measurement))
            rows_resolved += 1 + len(measurement.inputs)
            resolved_scoped_scenario = True

        dispatch_scenario_id = self._entity_id(context, "dispatch_scenario")
        if dispatch_scenario_id is not None and self._dispatch is not None:
            dispatch_scenario, failure = await self._dispatch_scenario(
                context,
                dispatch_scenario_id,
            )
            if failure is not None:
                return failure
            assert dispatch_scenario is not None
            data.update(self._dispatch_scenario_outputs(dispatch_scenario))
            rows_resolved += 1
            resolved_scoped_scenario = True

        if resolved_scoped_scenario:
            return AgentToolResult(
                status="success",
                data=data,
                rows=max(rows_resolved, 1),
            )

        resolved, failure = await self._resolved_workflow_inputs(context)
        if failure is not None:
            return failure
        if resolved is not None:
            data.update(
                {
                    "carbon_measurement_id": str(resolved.measurement.id),
                    "measurement_id": str(resolved.measurement.id),
                    "activity_record_ids": [str(resolved.activity.id)],
                    "current_product_id": str(resolved.current_product.id),
                    "calculation_run_id": str(resolved.measurement.calculation_run_id),
                    "metric_key": CARBON_METRIC_KEY,
                    "method_definition_id": (
                        str(resolved.method.id) if resolved.method is not None else None
                    ),
                }
            )
            return AgentToolResult(
                status="success",
                data=data,
                rows=resolved.rows_resolved,
            )

        return AgentToolResult(
            status="success",
            data=data,
            rows=(
                1
                + int(context.site_id is not None)
                + int(context.reporting_period_id is not None)
            ),
        )

    async def _retrieve_ledger_facts(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, RetrieveLedgerFactsInput):
            return self._invalid_arguments("retrieve_ledger_facts")
        measurement, failure = await self._measurement_detail(context)
        if failure is not None:
            return failure
        assert measurement is not None
        if arguments.metric_keys and measurement.metric_key not in arguments.metric_keys:
            return AgentToolResult(
                status="no_data",
                code="measurement_metric_outside_scope",
                message="The verified measurement does not match the requested metric scope.",
            )
        fact, failure = self._measurement_fact(measurement)
        if failure is not None:
            return failure
        assert fact is not None
        return AgentToolResult(
            status="success",
            data={
                **self._measurement_outputs(measurement),
                "fact_ids": [str(fact.fact_id)],
                "ledger_event_ids": [str(fact.ledger_event_id)],
            },
            facts=(fact,),
            rows=1,
        )

    async def _validate_activity(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, ValidateActivityInput):
            return self._invalid_arguments("validate_activity")
        measurement, failure = await self._measurement_for_activities(
            context,
            arguments.activity_record_ids,
        )
        if failure is not None:
            return failure
        assert measurement is not None
        if measurement.status != "verified":
            return AgentToolResult(
                status="validation_failed",
                code="measurement_not_verified",
                message="Only a verified measurement can enter the agent workflow.",
            )
        return AgentToolResult(
            status="success",
            data=self._measurement_outputs(measurement),
            rows=len(measurement.inputs),
        )

    async def _normalize_unit(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, NormalizeUnitInput):
            return self._invalid_arguments("normalize_unit")
        measurement, failure = await self._measurement_for_activities(
            context,
            arguments.activity_record_ids,
        )
        if failure is not None:
            return failure
        assert measurement is not None
        canonical = "kWh" if measurement.metric_key == "emissions.scope2.location_based" else "kg"
        if arguments.canonical_unit.casefold() != canonical.casefold():
            return AgentToolResult(status="validation_failed", code="measurement_canonical_unit_mismatch",
                                   message="The requested unit does not match the verified activity dimension.")
        return AgentToolResult(
            status="success",
            data={
                **self._measurement_outputs(measurement),
                "canonical_unit": canonical,
            },
            rows=len(measurement.inputs),
        )

    async def _select_emission_factor(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, SelectEmissionFactorInput):
            return self._invalid_arguments("select_emission_factor")
        measurement, failure = await self._measurement_for_activities(
            context,
            arguments.activity_record_ids,
        )
        if failure is not None:
            return failure
        assert measurement is not None
        if arguments.metric_key != measurement.metric_key:
            return AgentToolResult(
                status="validation_failed",
                code="measurement_metric_mismatch",
                message="The requested metric does not match the verified measurement.",
            )
        factor_ids = {item.emission_factor_id for item in measurement.calculations if item.emission_factor_id is not None}
        factor_id = next(iter(factor_ids)) if len(factor_ids) == 1 else None
        return AgentToolResult(
            status="success",
            data={
                **self._measurement_outputs(measurement),
                "emission_factor_id": str(factor_id) if factor_id is not None else None,
                "factor_set_hash": self._factor_set_hash(measurement),
            },
            rows=len(measurement.calculations),
        )

    async def _calculate_emissions(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, CalculateEmissionsInput):
            return self._invalid_arguments("calculate_emissions")
        measurement, failure = await self._measurement_for_activities(
            context,
            arguments.activity_record_ids,
        )
        if failure is not None:
            return failure
        assert measurement is not None
        if measurement.calculation_run.method_definition_id != arguments.method_definition_id:
            return AgentToolResult(
                status="stale",
                code="measurement_method_mismatch",
                message="The verified measurement was produced with a different method.",
            )
        factor_ids = {calculation.emission_factor_id for calculation in measurement.calculations}
        if ((arguments.factor_set_hash is not None and arguments.factor_set_hash != self._factor_set_hash(measurement))
                or (arguments.emission_factor_id is not None and factor_ids != {arguments.emission_factor_id})
                or (arguments.factor_set_hash is None and (arguments.emission_factor_id is None
                                                          or factor_ids != {arguments.emission_factor_id}))):
            return AgentToolResult(
                status="stale",
                code="measurement_factor_mismatch",
                message="The verified measurement was produced with a different factor set.",
            )
        fact, failure = self._measurement_fact(measurement)
        if failure is not None:
            return failure
        assert fact is not None
        return AgentToolResult(
            status="success",
            data={
                **self._measurement_outputs(measurement),
                "fact_ids": [str(fact.fact_id)],
                "ledger_event_ids": [str(fact.ledger_event_id)],
                "payload_hash": measurement.output_hash,
            },
            facts=(fact,),
            rows=len(measurement.calculations),
        )

    async def _calculate_confidence(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, CalculateConfidenceInput):
            return self._invalid_arguments("calculate_confidence")
        measurement, failure = await self._measurement_detail(context)
        if failure is not None:
            return failure
        assert measurement is not None
        if arguments.measurement_id is not None and arguments.measurement_id != measurement.id:
            return self._stale_measurement_target()
        if (
            arguments.calculation_run_id is not None
            and arguments.calculation_run_id != measurement.calculation_run.id
        ):
            return self._stale_measurement_target()
        if arguments.method_definition_id != measurement.calculation_run.method_definition_id:
            return AgentToolResult(
                status="stale",
                code="measurement_method_mismatch",
                message="The confidence belongs to a different deterministic method.",
            )
        return AgentToolResult(
            status="success",
            data={
                **self._measurement_outputs(measurement),
                "confidence": str(measurement.confidence),
            },
            rows=1,
        )

    async def _map_standard_requirement(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, MapStandardRequirementInput):
            return self._invalid_arguments("map_standard_requirement")
        if self._assurance is None:
            return AgentToolResult.unsupported("map_standard_requirement")
        try:
            standards = await self._assurance.list_standards(
                company_id=context.company_id,
                active_only=True,
                limit=100,
                offset=0,
            )
        except AssuranceError as error:
            return self._assurance_failure(error)
        standard = next(
            (item for item in standards.items if item.id == arguments.standard_id),
            None,
        )
        if standard is None:
            return AgentToolResult(
                status="no_data",
                code="assurance_standard_not_found",
                message="The requested active Assurance standard was not found.",
            )
        available = {item.id for item in standard.requirements if item.is_active}
        requested = set(arguments.requirement_ids) or available
        if not requested or not requested.issubset(available):
            return AgentToolResult(
                status="validation_failed",
                code="assurance_requirement_scope_invalid",
                message="One or more requested requirements are outside the active standard.",
            )
        draft_id = self._assurance_draft_id(context)
        data: dict[str, object] = {
            "standard_id": str(standard.id),
            "requirement_ids": [str(item) for item in sorted(requested, key=str)],
        }
        if draft_id is not None:
            data["disclosure_draft_id"] = str(draft_id)
        return AgentToolResult(status="success", data=data, rows=len(requested))

    async def _decompose_claim(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        """Run the landed atomic-claim validator and expose its persisted outputs.

        Assurance owns decomposition, retrieval, binding, citation validation,
        gap detection and preview creation as one transaction.  The frozen agent
        tools replay and validate the corresponding persisted stages; they do not
        duplicate any of that domain logic.
        """

        if not isinstance(arguments, DecomposeClaimInput):
            return self._invalid_arguments("decompose_claim")
        if self._assurance is None:
            return AgentToolResult.unsupported("decompose_claim")
        draft, failure = await self._assurance_draft(context, arguments.disclosure_draft_id)
        if failure is not None:
            return failure
        assert draft is not None
        if draft.standard.id != self._entity_id(context, "standard") and self._entity_id(
            context, "standard"
        ) is not None:
            return self._stale_assurance_scope()
        if draft.site_id != context.site_id or draft.reporting_period_id != context.reporting_period_id:
            return self._stale_assurance_scope()
        frozen_evidence_ids = frozenset(
            entity.entity_id
            for entity in context.entities
            if entity.entity_type == "evidence_item"
        )
        try:
            validated = await self._assurance.validate_for_agent(
                draft.id,
                DisclosureDraftValidateRequest(
                    company_id=context.company_id,
                    requested_by=context.actor_id,
                    expected_context_hash=draft.context_hash,
                    idempotency_key=(
                        f"agent:{context.request_hash}:assurance:{draft.id}"
                    ),
                ),
                allowed_evidence_item_ids=(
                    frozen_evidence_ids if frozen_evidence_ids else None
                ),
            )
        except AssuranceError as error:
            return self._assurance_failure(error)
        draft, failure = await self._assurance_draft(context, draft.id)
        if failure is not None:
            return failure
        assert draft is not None
        matching = [item for item in draft.claims if item.requirement_id == arguments.requirement_id]
        if not matching:
            return AgentToolResult(
                status="no_data",
                code="assurance_claim_not_found",
                message="No atomic claim was created for the requested requirement.",
            )
        if len(draft.claims) > arguments.max_claims:
            return AgentToolResult(
                status="validation_failed",
                code="assurance_claim_budget_exceeded",
                message="The persisted draft exceeds the requested atomic-claim limit.",
            )
        scoped_claim = matching[0]
        primary = next(
            (
                item
                for item in draft.claims
                if item.fact_binding_id is not None and item.ledger_event_id is not None
            ),
            scoped_claim,
        )
        ledger_event_ids = sorted(
            {
                item.ledger_event_id
                for item in draft.claims
                if item.ledger_event_id is not None
            },
            key=str,
        )
        data: dict[str, object] = {
            "disclosure_draft_id": str(draft.id),
            "claim_id": str(primary.id),
            "claim_ids": [str(item.id) for item in draft.claims],
            "requirement_id": str(arguments.requirement_id),
            "requirement_ids": [
                str(item.requirement_id)
                for item in draft.claims
                if item.requirement_id is not None
            ],
            "ledger_event_ids": [str(item) for item in ledger_event_ids],
            "evidence_query": scoped_claim.rendered_text or scoped_claim.claim_template,
            "assurance_terminal_state": validated.terminal_state,
            "payload_hash": draft.payload_hash,
            "target_type": "disclosure_draft",
            "target_id": str(draft.id),
            "approval_target_type": "disclosure_draft",
            "approval_target_id": str(draft.id),
        }
        if draft.approval is not None:
            data.update(
                {
                    "approval_id": str(draft.approval.id),
                    "preview_hash": draft.approval.preview_hash,
                    "approval_expires_at": draft.approval.expires_at.isoformat(),
                    "approval_context_hash": draft.approval.context_hash,
                }
            )
        return AgentToolResult(
            status=(
                "unsupported"
                if validated.terminal_state == "unsupported"
                else "success"
            ),
            data=data,
            rows=len(draft.claims),
            code=(
                "assurance_required_claim_unsupported"
                if validated.terminal_state == "unsupported"
                else None
            ),
            message=(
                "Required unsupported claims or evidence gaps block this disclosure."
                if validated.terminal_state == "unsupported"
                else None
            ),
        )

    async def _retrieve_evidence(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, RetrieveEvidenceInput):
            return self._invalid_arguments("retrieve_evidence")
        if self._assurance is None:
            return AgentToolResult.unsupported("retrieve_evidence")
        draft_id = self._assurance_draft_id(context)
        if draft_id is None:
            return self._clarification(
                code="assurance_draft_required",
                field="context.disclosure_draft_id",
                message="A persisted disclosure draft is required for evidence retrieval.",
            )
        draft, failure = await self._assurance_draft(context, draft_id)
        if failure is not None:
            return failure
        assert draft is not None
        claims = [
            item
            for item in draft.claims
            if arguments.requirement_id is None or item.requirement_id == arguments.requirement_id
        ]
        if not claims:
            return AgentToolResult(
                status="no_data",
                code="assurance_claim_not_found",
                message="No persisted claim matches the requested evidence scope.",
            )
        normalized_query = self._normalized_reference(arguments.query_text)
        if not any(
            normalized_query
            in self._normalized_reference(item.rendered_text or item.claim_template)
            or self._normalized_reference(item.rendered_text or item.claim_template)
            in normalized_query
            for item in claims
        ):
            return AgentToolResult(
                status="validation_failed",
                code="assurance_evidence_query_mismatch",
                message="The evidence query is not bound to a persisted atomic claim.",
            )
        evidence = []
        for claim in claims:
            for citation in claim.citations:
                item = citation.evidence
                if item is None:
                    continue
                if arguments.evidence_types and item.evidence_type not in arguments.evidence_types:
                    continue
                similarity = item.similarity or Decimal(0)
                if similarity < arguments.min_support_score:
                    continue
                evidence.append(item)
        by_id = {item.id: item for item in evidence}
        selected = sorted(by_id.values(), key=lambda item: str(item.id))[: arguments.limit]
        if not selected and all(
            item.fact_binding_id is not None and item.ledger_event_id is not None
            for item in claims
        ):
            return AgentToolResult(
                status="success",
                code="assurance_ledger_support_only",
                message="The scoped claims are fully ledger-bound and require no document chunks.",
                data={
                    "disclosure_draft_id": str(draft.id),
                    "claim_ids": [str(item.id) for item in claims],
                    "evidence_item_ids": [],
                },
                rows=0,
                chunks=0,
            )
        if not selected:
            return AgentToolResult(
                status="no_data",
                code="assurance_evidence_not_found",
                message="No persisted evidence satisfies the requested support threshold.",
            )
        return AgentToolResult(
            status="success",
            data={
                "disclosure_draft_id": str(draft.id),
                "claim_ids": [str(item.id) for item in claims],
                "evidence_item_ids": [str(item.id) for item in selected],
            },
            rows=len(selected),
            chunks=len(selected),
        )

    async def _bind_claim_facts(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, BindClaimFactsInput):
            return self._invalid_arguments("bind_claim_facts")
        draft, failure = await self._draft_for_claim(context, arguments.claim_id)
        if failure is not None:
            return failure
        assert draft is not None
        claim = next(item for item in draft.claims if item.id == arguments.claim_id)
        if claim.fact_binding_id is None or claim.ledger_event_id is None:
            return AgentToolResult(
                status="unsupported",
                code="assurance_fact_binding_missing",
                message="The selected atomic claim has no persisted numerical fact binding.",
            )
        if claim.ledger_event_id not in arguments.ledger_event_ids:
            return AgentToolResult(
                status="stale",
                code="assurance_ledger_binding_mismatch",
                message="The supplied ledger facts do not contain the persisted claim binding.",
            )
        metric_key = claim.validation_details.get("metric_key")
        if not isinstance(metric_key, str) or not metric_key.strip():
            return AgentToolResult(
                status="validation_failed",
                code="assurance_metric_binding_missing",
                message="The persisted numerical claim has no validated metric binding.",
            )
        metric_key = metric_key.strip()
        if context.metric_keys and metric_key not in context.metric_keys:
            return AgentToolResult(
                status="stale",
                code="assurance_metric_scope_mismatch",
                message="The persisted claim metric is outside the frozen metric scope.",
            )
        fact = AgentToolFact(
            fact_id=claim.fact_binding_id,
            metric_key=metric_key,
            ledger_event_id=claim.ledger_event_id,
            display_value=claim.rendered_text or claim.claim_template,
        )
        return AgentToolResult(
            status="success",
            data={
                "disclosure_draft_id": str(draft.id),
                "claim_id": str(claim.id),
                "claim_ids": [str(item.id) for item in draft.claims],
                "fact_ids": [str(claim.fact_binding_id)],
                "ledger_event_ids": [str(claim.ledger_event_id)],
            },
            facts=(fact,),
            rows=1,
        )

    async def _validate_citations(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, ValidateCitationsInput):
            return self._invalid_arguments("validate_citations")
        draft, failure = await self._assurance_draft(context, self._assurance_draft_id(context))
        if failure is not None:
            return failure
        assert draft is not None
        by_id = {item.id: item for item in draft.claims}
        if not set(arguments.claim_ids).issubset(by_id):
            return AgentToolResult(
                status="stale",
                code="assurance_claim_set_mismatch",
                message="The citation request contains a claim outside the persisted draft.",
            )
        citations = [citation for claim_id in arguments.claim_ids for citation in by_id[claim_id].citations]
        invalid = [item for item in citations if item.validation_status != "valid"]
        if invalid:
            return AgentToolResult(
                status="validation_failed",
                code="assurance_citation_invalid",
                message="One or more persisted citations failed deterministic validation.",
                rows=len(citations),
            )
        return AgentToolResult(
            status="success",
            data={
                "disclosure_draft_id": str(draft.id),
                "claim_ids": [str(item) for item in arguments.claim_ids],
                "citation_ids": [str(item.id) for item in citations],
                "evidence_item_ids": [
                    str(item.evidence_item_id)
                    for item in citations
                    if item.evidence_item_id is not None
                ],
            },
            rows=len(citations),
        )

    async def _detect_evidence_gaps(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, DetectEvidenceGapsInput):
            return self._invalid_arguments("detect_evidence_gaps")
        draft, failure = await self._assurance_draft(context, arguments.disclosure_draft_id)
        if failure is not None:
            return failure
        assert draft is not None
        if arguments.claim_ids and not set(arguments.claim_ids).issubset(
            {item.id for item in draft.claims}
        ):
            return AgentToolResult(
                status="stale",
                code="assurance_claim_set_mismatch",
                message="The gap request contains a claim outside the persisted draft.",
            )
        open_gaps = [item for item in draft.gaps if item.status == "open"]
        data = {
            "disclosure_draft_id": str(draft.id),
            "claim_ids": [str(item.id) for item in draft.claims],
            "evidence_gap_ids": [str(item.id) for item in open_gaps],
            "payload_hash": draft.payload_hash,
            "target_type": "disclosure_draft",
            "target_id": str(draft.id),
            "approval_target_type": "disclosure_draft",
            "approval_target_id": str(draft.id),
        }
        if draft.status == "blocked" or draft.validation_summary.get(
            "terminal_state"
        ) == "unsupported":
            return AgentToolResult(
                status="unsupported",
                code="assurance_evidence_gaps_blocked",
                message="Required unsupported claims or evidence gaps block this disclosure.",
                data=data,
                rows=len(open_gaps),
            )
        if draft.status != "pending_approval" or draft.approval is None:
            return AgentToolResult(
                status="validation_failed",
                code="assurance_preview_missing",
                message="The validated disclosure has no pending approval preview.",
                data=data,
                rows=len(open_gaps),
            )
        data.update(
            {
                "approval_id": str(draft.approval.id),
                "preview_hash": draft.approval.preview_hash,
                "approval_expires_at": draft.approval.expires_at.isoformat(),
                "approval_context_hash": draft.approval.context_hash,
            }
        )
        return AgentToolResult(status="success", data=data, rows=len(open_gaps))

    async def _load_supplier_candidates(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, LoadSupplierCandidatesInput):
            return self._invalid_arguments("load_supplier_candidates")
        scenario_id = arguments.scenario_id or self._entity_id(context, "procurement_scenario")
        if scenario_id is None:
            if self._procurement is None:
                return AgentToolResult.unsupported("load_supplier_candidates")
            try:
                products = await self._procurement.list_supplier_products(
                    company_id=context.company_id,
                    supplier_id=None,
                    material_code=arguments.material_code,
                    category=None,
                    active_only=True,
                    search=None,
                    limit=arguments.limit,
                    offset=0,
                )
            except ProcurementError as error:
                return self._procurement_failure(error)
            return AgentToolResult(
                status="needs_clarification",
                code="procurement_scenario_required",
                message=(
                    "Candidates are available, but scoring requires an already frozen "
                    "Procurement scenario under the landed service contract."
                ),
                data={
                    "required_fields": ["context.procurement_scenario_id"],
                    "code": "procurement_scenario_required",
                    "supplier_product_ids": [str(item.id) for item in products.items],
                },
                rows=len(products.items),
            )

        scenario, failure = await self._scenario(context, scenario_id)
        if failure is not None:
            return failure
        assert scenario is not None
        candidate_ids = tuple(item.product.id for item in scenario.alternatives)
        scoped_ids = set(context.supplier_product_ids) - {scenario.current_product.id}
        if scoped_ids and scoped_ids != set(candidate_ids):
            return AgentToolResult(
                status="stale",
                code="supplier_scope_mismatch",
                message="The frozen scenario candidates do not match the agent request scope.",
            )
        if (
            scenario.terminal_state == "no_feasible_option"
            or scenario.selected_recommendation is None
        ):
            return AgentToolResult(
                status="no_feasible_option",
                data=self._scenario_outputs(scenario),
                rows=len(candidate_ids),
                code="no_feasible_supplier",
                message="No candidate satisfies the frozen hard constraints.",
            )
        return AgentToolResult(
            status="success",
            data=self._scenario_outputs(scenario),
            rows=len(candidate_ids),
        )

    async def _score_supplier(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, ScoreSupplierInput):
            return self._invalid_arguments("score_supplier")
        scenario, failure = await self._scenario(context, arguments.scenario_id)
        if failure is not None:
            return failure
        assert scenario is not None
        candidate_ids = {item.product.id for item in scenario.alternatives}
        if set(arguments.supplier_product_ids) != candidate_ids:
            return AgentToolResult(
                status="stale",
                code="supplier_candidate_set_mismatch",
                message="The supplied candidates do not exactly match the frozen scenario.",
            )
        if arguments.method_definition_id != scenario.method.id:
            return AgentToolResult(
                status="stale",
                code="supplier_scoring_method_mismatch",
                message="The frozen scenario uses a different supplier-scoring method.",
            )
        if scenario.terminal_state == "no_feasible_option":
            return AgentToolResult(
                status="no_feasible_option",
                data=self._scenario_outputs(scenario),
                rows=len(scenario.alternatives),
                code="no_feasible_supplier",
                message="No candidate satisfies the frozen hard constraints.",
            )
        return AgentToolResult(
            status="success",
            data=self._scenario_outputs(scenario),
            rows=len(scenario.alternatives),
        )

    async def _calculate_procurement_impact(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, CalculateProcurementImpactInput):
            return self._invalid_arguments("calculate_procurement_impact")
        scenario, failure = await self._scenario(context, arguments.scenario_id)
        if failure is not None:
            return failure
        assert scenario is not None
        score_ids = {item.score_id for item in scenario.alternatives}
        if set(arguments.supplier_score_ids) != score_ids:
            return AgentToolResult(
                status="stale",
                code="supplier_score_set_mismatch",
                message="The requested scores do not exactly match the frozen scenario.",
            )
        if scenario.selected_recommendation is None:
            return AgentToolResult(
                status="no_feasible_option",
                data=self._scenario_outputs(scenario),
                rows=len(scenario.alternatives),
                code="no_feasible_supplier",
                message="No candidate satisfies the frozen hard constraints.",
            )
        selected = next(
            (
                item
                for item in scenario.alternatives
                if item.product.id == scenario.selected_recommendation.recommended_product_id
            ),
            None,
        )
        if selected is None:
            return AgentToolResult(
                status="validation_failed",
                code="procurement_recommendation_integrity_error",
                message="The selected recommendation has no matching frozen supplier score.",
            )
        return AgentToolResult(
            status="success",
            data={
                **self._scenario_outputs(scenario),
                "selected_supplier_score_id": str(selected.score_id),
                "projected_footprint_kgco2e": str(
                    selected.impact.projected_footprint_kgco2e
                ),
                "avoided_kgco2e": str(selected.impact.avoided_kgco2e),
                "reduction_pct": str(selected.impact.reduction_pct),
                "cost_delta_pct": str(selected.impact.cost_delta_pct),
            },
            rows=len(scenario.alternatives),
        )

    async def _build_procurement_recommendation(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, BuildProcurementRecommendationInput):
            return self._invalid_arguments("build_procurement_recommendation")
        scenario, failure = await self._scenario(context, arguments.scenario_id)
        if failure is not None:
            return failure
        assert scenario is not None
        recommendation = scenario.selected_recommendation
        if recommendation is None:
            return AgentToolResult(
                status="no_feasible_option",
                data=self._scenario_outputs(scenario),
                code="no_feasible_supplier",
                message="No candidate satisfies the frozen hard constraints.",
            )
        selected = next(
            (
                item
                for item in scenario.alternatives
                if item.product.id == recommendation.recommended_product_id
            ),
            None,
        )
        if selected is None or selected.score_id != arguments.selected_supplier_score_id:
            return AgentToolResult(
                status="stale",
                code="selected_supplier_score_mismatch",
                message="The selected score does not match the frozen recommendation.",
            )
        assert self._procurement is not None
        try:
            detail = await self._procurement.get_recommendation(
                company_id=context.company_id,
                recommendation_id=recommendation.id,
            )
        except ProcurementError as error:
            return self._procurement_failure(error)
        if detail.scenario_id != scenario.id or detail.supplier_score.score_id != selected.score_id:
            return AgentToolResult(
                status="validation_failed",
                code="procurement_recommendation_integrity_error",
                message="The persisted recommendation does not match the frozen scenario.",
            )
        if detail.ledger_event_id is None:
            return AgentToolResult(
                status="validation_failed",
                code="procurement_ledger_event_missing",
                message="The recommendation has no ledger event and cannot be returned.",
            )

        facts = (
            AgentToolFact(
                fact_id=detail.id,
                metric_key="procurement.projected_avoided_emissions",
                ledger_event_id=detail.ledger_event_id,
                display_value=f"{detail.avoided_kgco2e} kgCO2e",
            ),
            AgentToolFact(
                fact_id=detail.supplier_score.score_id,
                metric_key="procurement.cost_delta_pct",
                ledger_event_id=detail.ledger_event_id,
                display_value=f"{detail.cost_delta_pct}%",
            ),
        )
        approval = detail.approval
        return AgentToolResult(
            status="success",
            data={
                **self._scenario_outputs(scenario),
                "recommendation_id": str(detail.id),
                "target_type": "procurement_recommendation",
                "target_id": str(detail.id),
                "approval_target_type": "procurement_recommendation",
                "approval_target_id": str(detail.id),
                "payload_hash": detail.payload_hash,
                "ledger_event_ids": [str(detail.ledger_event_id)],
                "fact_ids": [str(fact.fact_id) for fact in facts],
                "approval_id": str(approval.id) if approval is not None else None,
                "preview_hash": approval.preview_hash if approval is not None else None,
                "approval_expires_at": (
                    approval.expires_at.isoformat() if approval is not None else None
                ),
            },
            facts=facts,
            rows=1,
        )

    async def _sync_grid_forecast(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, SyncGridForecastInput):
            return self._invalid_arguments("sync_grid_forecast")
        if self._dispatch is None:
            return AgentToolResult.unsupported("sync_grid_forecast")
        if context.site_id is None:
            return self._clarification(
                code="dispatch_site_required",
                field="context.site_id",
                message="A resolved site is required to synchronize a grid forecast.",
            )
        provider = self._forecast_provider(arguments.source_mode)
        if provider is None:
            return AgentToolResult(
                status="provider_unavailable",
                code="dispatch_forecast_provider_unavailable",
                message="The requested forecast provider mode is unavailable.",
            )
        try:
            result = await self._dispatch.sync_forecast(
                ForecastSyncRequest(
                    company_id=context.company_id,
                    site_id=context.site_id,
                    horizon_hours=arguments.horizon_hours,
                    source_mode=arguments.source_mode,
                ),
                provider=provider,
            )
        except DispatchError as error:
            return self._dispatch_failure(error)
        requested_start = arguments.forecast_start.astimezone(UTC)
        if result.forecast_start != requested_start:
            return AgentToolResult(
                status="stale",
                code="dispatch_forecast_start_mismatch",
                message="The immutable forecast does not begin at the requested UTC hour.",
                data={
                    "forecast_id": str(result.source_document_id),
                    "forecast_start": result.forecast_start.isoformat(),
                },
                rows=len(result.points),
            )
        return AgentToolResult(
            status="success",
            data={
                "forecast_id": str(result.source_document_id),
                "forecast_source_document_id": str(result.source_document_id),
                "forecast_start": result.forecast_start.isoformat(),
                "forecast_end": result.forecast_end.isoformat(),
                "grid_zone": result.zone,
                "payload_hash": result.snapshot_hash,
                "forecast_source_mode": result.source_mode,
            },
            rows=len(result.points),
            cache={"hit": result.inserted_points == 0 and result.existing_points > 0},
        )

    async def _optimize_dispatch_window(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, OptimizeDispatchWindowInput):
            return self._invalid_arguments("optimize_dispatch_window")
        if self._dispatch is None:
            return AgentToolResult.unsupported("optimize_dispatch_window")
        scoped_scenario_id = self._entity_id(context, "dispatch_scenario")
        if scoped_scenario_id is not None and scoped_scenario_id != arguments.scenario_id:
            return self._stale_dispatch_scope()
        scenario, failure = await self._dispatch_scenario(
            context,
            arguments.scenario_id,
            forecast_id=arguments.forecast_id,
            method_definition_id=arguments.method_definition_id,
        )
        if failure is not None:
            return failure
        assert scenario is not None
        try:
            result = await self._dispatch.optimize_scenario(
                arguments.scenario_id,
                OptimizeScenarioRequest(company_id=context.company_id),
            )
        except DispatchError as error:
            return self._dispatch_failure(error)
        if result.terminal_state == "no_feasible_option" or result.recommendation is None:
            return AgentToolResult(
                status="no_feasible_option",
                code="dispatch_no_feasible_window",
                message="No complete window satisfies the frozen Dispatch constraints.",
                data={
                    "dispatch_scenario_id": str(arguments.scenario_id),
                    "scenario_id": str(arguments.scenario_id),
                    "forecast_id": str(arguments.forecast_id),
                },
                rows=result.evaluated_windows,
            )
        recommendation = result.recommendation
        impact_method_id = recommendation.impact_snapshot.get("method_id")
        impact_forecast_id = recommendation.impact_snapshot.get(
            "forecast_source_document_id"
        )
        if (
            arguments.method_definition_id is not None
            and str(impact_method_id) != str(arguments.method_definition_id)
        ) or str(impact_forecast_id) != str(arguments.forecast_id):
            return AgentToolResult(
                status="stale",
                code="dispatch_optimizer_input_mismatch",
                message="The persisted recommendation does not match the requested inputs.",
            )
        return self._dispatch_recommendation_result(
            recommendation,
            evaluated_windows=result.evaluated_windows,
            feasible_windows=result.feasible_windows,
            dispatch_method_definition_id=str(impact_method_id),
        )

    async def _calculate_dispatch_impact(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        if not isinstance(arguments, CalculateDispatchImpactInput):
            return self._invalid_arguments("calculate_dispatch_impact")
        if self._dispatch is None:
            return AgentToolResult.unsupported("calculate_dispatch_impact")
        scoped_scenario_id = self._entity_id(context, "dispatch_scenario")
        if scoped_scenario_id is not None and scoped_scenario_id != arguments.scenario_id:
            return self._stale_dispatch_scope()
        _, failure = await self._dispatch_scenario(context, arguments.scenario_id)
        if failure is not None:
            return failure
        try:
            result = await self._dispatch.get_recommendation(
                company_id=context.company_id,
                scenario_id=arguments.scenario_id,
            )
        except DispatchError as error:
            return self._dispatch_failure(error)
        recommendation = result.recommendation
        if result.terminal_state == "no_feasible_option" or recommendation is None:
            return AgentToolResult(
                status="no_feasible_option",
                code="dispatch_no_feasible_window",
                message="No Dispatch recommendation exists because no window is feasible.",
                data={"dispatch_scenario_id": str(arguments.scenario_id)},
            )
        if recommendation.id != arguments.recommendation_id:
            return AgentToolResult(
                status="stale",
                code="dispatch_recommendation_mismatch",
                message="The requested recommendation is not selected for this scenario.",
            )
        return self._dispatch_recommendation_result(recommendation)

    async def _create_approval_preview(
        self,
        *,
        context: AgentToolContext,
        arguments: StrictToolInput,
    ) -> AgentToolResult:
        """Validate and replay an exact existing preview; never decide one."""

        if not isinstance(arguments, CreateApprovalPreviewInput):
            return self._invalid_arguments("create_approval_preview")
        if arguments.target_type == "disclosure_draft":
            return await self._assurance_approval_preview(context, arguments)
        if arguments.target_type == "dispatch_recommendation":
            return await self._dispatch_approval_preview(context, arguments)
        if arguments.target_type != "procurement_recommendation" or self._procurement is None:
            return AgentToolResult.unsupported("create_approval_preview")
        try:
            detail = await self._procurement.get_recommendation(
                company_id=context.company_id,
                recommendation_id=arguments.target_id,
            )
        except ProcurementError as error:
            return self._procurement_failure(error)

        scenario, failure = await self._scenario(context, detail.scenario_id)
        if failure is not None:
            return failure
        assert scenario is not None
        selected = scenario.selected_recommendation
        if selected is None or selected.id != detail.id:
            return AgentToolResult(
                status="stale",
                code="approval_target_changed",
                message="The approval target is no longer the selected recommendation.",
            )
        if detail.invalidated_at is not None or detail.payload_hash != arguments.payload_hash:
            return AgentToolResult(
                status="stale",
                code="approval_preview_hash_mismatch",
                message="The persisted recommendation no longer matches the requested preview.",
            )

        approval = detail.approval
        if approval is None:
            return AgentToolResult(
                status="validation_failed",
                code="approval_preview_missing",
                message="The recommendation has no persisted approval preview.",
            )
        if (
            approval.preview_hash != detail.payload_hash
            or approval.analysis_signature != detail.analysis_signature
        ):
            return AgentToolResult(
                status="stale",
                code="approval_preview_binding_mismatch",
                message="The approval preview is not bound to the current recommendation.",
            )
        expires_at = self._as_utc(approval.expires_at)
        if arguments.expires_at is not None and self._as_utc(arguments.expires_at) != expires_at:
            return AgentToolResult(
                status="stale",
                code="approval_preview_expiry_mismatch",
                message="The approval preview expiry no longer matches the reviewed payload.",
            )
        if (
            expires_at <= datetime.now(UTC)
            or approval.status != "pending"
            or detail.status != "pending_approval"
        ):
            return AgentToolResult(
                status="stale",
                code="approval_preview_not_pending",
                message="The approval preview is expired or no longer pending.",
            )

        try:
            preview_current = await self._procurement.is_recommendation_preview_current(
                company_id=context.company_id,
                recommendation_id=detail.id,
            )
        except ProcurementError as error:
            return self._procurement_failure(error)
        if not preview_current:
            return AgentToolResult(
                status="stale",
                code="approval_upstream_changed",
                message="An upstream fact or method changed after the preview was created.",
            )

        return AgentToolResult(
            status="approval_required",
            code="human_approval_required",
            message="A human must decide the exact persisted approval preview.",
            data={
                "approval_id": str(approval.id),
                "target_type": arguments.target_type,
                "target_id": str(detail.id),
                "preview_hash": approval.preview_hash,
                "analysis_signature": approval.analysis_signature,
                "approval_context_hash": (
                    getattr(approval, "context_hash", None) or detail.analysis_signature
                ),
                "expires_at": expires_at.isoformat(),
            },
            rows=1,
        )

    async def _assurance_approval_preview(
        self,
        context: AgentToolContext,
        arguments: CreateApprovalPreviewInput,
    ) -> AgentToolResult:
        if self._assurance is None:
            return AgentToolResult.unsupported("create_approval_preview")
        draft, failure = await self._assurance_draft(context, arguments.target_id)
        if failure is not None:
            return failure
        assert draft is not None
        if draft.invalidated_at is not None or draft.payload_hash != arguments.payload_hash:
            return self._stale_approval_hash()
        try:
            # The public evidence-pack read revalidates measurement, evidence,
            # requirement and method dependencies before an interrupt is emitted.
            await self._assurance.get_evidence_pack(
                company_id=context.company_id,
                draft_id=draft.id,
            )
        except AssuranceError as error:
            return self._assurance_failure(error)
        approval = draft.approval
        if approval is None:
            return self._missing_approval_preview()
        return self._approval_required_result(
            context=context,
            arguments=arguments,
            target_status=draft.status,
            target_analysis_signature=str(
                draft.validation_summary.get("analysis_signature", approval.analysis_signature)
            ),
            target_context_hash=draft.context_hash,
            approval=approval,
        )

    async def _dispatch_approval_preview(
        self,
        context: AgentToolContext,
        arguments: CreateApprovalPreviewInput,
    ) -> AgentToolResult:
        if self._dispatch is None:
            return AgentToolResult.unsupported("create_approval_preview")
        scenario_id = self._entity_id(context, "dispatch_scenario")
        if scenario_id is None:
            return self._clarification(
                code="dispatch_scenario_required",
                field="context.dispatch_scenario_id",
                message="A frozen Dispatch scenario is required to validate its preview.",
            )
        _, failure = await self._dispatch_scenario(context, scenario_id)
        if failure is not None:
            return failure
        try:
            # Idempotent optimization revalidates load/method/policy snapshots and
            # every immutable forecast point before replaying the recommendation.
            replay = await self._dispatch.optimize_scenario(
                scenario_id,
                OptimizeScenarioRequest(company_id=context.company_id),
            )
        except DispatchError as error:
            return self._dispatch_failure(error)
        recommendation = replay.recommendation
        if recommendation is None or recommendation.id != arguments.target_id:
            return AgentToolResult(
                status="stale",
                code="approval_target_changed",
                message="The Dispatch approval target is no longer selected.",
            )
        if (
            recommendation.invalidated_at is not None
            or recommendation.payload_hash != arguments.payload_hash
        ):
            return self._stale_approval_hash()
        return self._approval_required_result(
            context=context,
            arguments=arguments,
            target_status=recommendation.status,
            target_analysis_signature=recommendation.analysis_signature,
            target_context_hash=recommendation.approval.context_hash,
            approval=recommendation.approval,
        )

    def _approval_required_result(
        self,
        *,
        context: AgentToolContext,
        arguments: CreateApprovalPreviewInput,
        target_status: str,
        target_analysis_signature: str,
        target_context_hash: str | None,
        approval: object,
    ) -> AgentToolResult:
        preview_hash = str(approval.preview_hash)
        analysis_signature = str(approval.analysis_signature)
        approval_status = str(approval.status)
        approval_target_id = getattr(approval, "target_id", arguments.target_id)
        approval_target_type = getattr(approval, "target_type", arguments.target_type)
        expires_at = self._as_utc(approval.expires_at)
        if (
            preview_hash != arguments.payload_hash
            or analysis_signature != target_analysis_signature
            or approval_target_id != arguments.target_id
            or approval_target_type != arguments.target_type
        ):
            return AgentToolResult(
                status="stale",
                code="approval_preview_binding_mismatch",
                message="The approval preview is not bound to the current target.",
            )
        if arguments.expires_at is not None and self._as_utc(arguments.expires_at) != expires_at:
            return AgentToolResult(
                status="stale",
                code="approval_preview_expiry_mismatch",
                message="The approval preview expiry no longer matches the reviewed payload.",
            )
        if expires_at <= datetime.now(UTC) or approval_status != "pending" or target_status != (
            "pending_approval"
        ):
            return AgentToolResult(
                status="stale",
                code="approval_preview_not_pending",
                message="The approval preview is expired or no longer pending.",
            )
        return AgentToolResult(
            status="approval_required",
            code="human_approval_required",
            message="A human must decide the exact persisted approval preview.",
            data={
                "approval_id": str(approval.id),
                "target_type": arguments.target_type,
                "target_id": str(arguments.target_id),
                "preview_hash": preview_hash,
                "analysis_signature": analysis_signature,
                "approval_context_hash": (
                    target_context_hash or context.analysis_signature
                ),
                "expires_at": expires_at.isoformat(),
            },
            rows=1,
        )

    @staticmethod
    def _dispatch_recommendation_result(
        recommendation: object,
        *,
        evaluated_windows: int = 0,
        feasible_windows: int = 0,
        dispatch_method_definition_id: str | None = None,
    ) -> AgentToolResult:
        recommendation_id = recommendation.id
        scenario_id = recommendation.scenario_id
        ledger_event_id = recommendation.ledger_event_id
        approval = recommendation.approval
        avoided = recommendation.avoided_kgco2e
        expected = recommendation.expected_emissions_kgco2e
        facts = (
            AgentToolFact(
                fact_id=recommendation_id,
                metric_key="dispatch.avoided_emissions",
                ledger_event_id=ledger_event_id,
                display_value=f"{avoided} kgCO2e",
            ),
            AgentToolFact(
                fact_id=scenario_id,
                metric_key="dispatch.expected_emissions",
                ledger_event_id=ledger_event_id,
                display_value=f"{expected} kgCO2e",
            ),
        )
        return AgentToolResult(
            status="success",
            data={
                "dispatch_scenario_id": str(scenario_id),
                "scenario_id": str(scenario_id),
                "dispatch_recommendation_id": str(recommendation_id),
                "recommendation_id": str(recommendation_id),
                "dispatch_method_definition_id": (
                    dispatch_method_definition_id
                    or str(recommendation.impact_snapshot.get("method_id"))
                ),
                "target_type": "dispatch_recommendation",
                "target_id": str(recommendation_id),
                "approval_target_type": "dispatch_recommendation",
                "approval_target_id": str(recommendation_id),
                "payload_hash": recommendation.payload_hash,
                "ledger_event_ids": [str(ledger_event_id)],
                "evidence_item_ids": [
                    str(item) for item in recommendation.evidence_item_ids
                ],
                "fact_ids": [str(item.fact_id) for item in facts],
                "approval_id": str(approval.id),
                "preview_hash": approval.preview_hash,
                "approval_expires_at": approval.expires_at.isoformat(),
                "recommended_start": recommendation.recommended_start.isoformat(),
                "recommended_end": recommendation.recommended_end.isoformat(),
                "expected_emissions_kgco2e": str(expected),
                "baseline_emissions_kgco2e": str(
                    recommendation.baseline_emissions_kgco2e
                ),
                "avoided_kgco2e": str(avoided),
                "reduction_pct": str(recommendation.reduction_pct),
                "evaluated_windows": evaluated_windows,
                "feasible_windows": feasible_windows,
                "actuation_authorized": False,
            },
            facts=facts,
            rows=max(evaluated_windows, 1),
        )

    async def _measurement_for_activities(
        self,
        context: AgentToolContext,
        activity_record_ids: tuple[UUID, ...],
    ) -> tuple[MeasurementDetail | None, AgentToolResult | None]:
        measurement, failure = await self._measurement_detail(context)
        if failure is not None:
            return None, failure
        assert measurement is not None
        persisted_ids = tuple(item.activity_record_id for item in measurement.inputs)
        if not activity_record_ids and self._measurement_id(context) == measurement.id:
            # The immutable measurement is the exact source set. This keeps a
            # full-quarter replay bounded without copying thousands of IDs into
            # every graph checkpoint; the domain detail verifies all sources.
            return measurement, None
        if len(persisted_ids) != len(activity_record_ids) or set(persisted_ids) != set(
            activity_record_ids
        ):
            return None, AgentToolResult(
                status="stale",
                code="measurement_activity_set_mismatch",
                message="The activity set does not exactly match the verified measurement.",
            )
        return measurement, None

    async def _measurement_detail(
        self,
        context: AgentToolContext,
        *,
        measurement_id: UUID | None = None,
        allow_resolution: bool = True,
    ) -> tuple[MeasurementDetail | None, AgentToolResult | None]:
        if self._measurement is None:
            return None, AgentToolResult.unsupported("retrieve_ledger_facts")
        resolved_id = measurement_id or self._measurement_id(context)
        if resolved_id is None:
            scenario_id = self._entity_id(context, "procurement_scenario")
            if scenario_id is not None and self._procurement is not None:
                scenario, failure = await self._scenario(context, scenario_id)
                if failure is not None:
                    return None, failure
                assert scenario is not None
                resolved_id = scenario.carbon_measurement_id
        if resolved_id is None and allow_resolution:
            resolved, failure = await self._resolved_workflow_inputs(context)
            if failure is not None:
                return None, failure
            if resolved is not None:
                resolved_id = resolved.measurement.id
        if resolved_id is None:
            return None, AgentToolResult(
                status="needs_clarification",
                code="verified_measurement_required",
                message="A unique verified measurement is required for this tool.",
                data={
                    "required_fields": [
                        "context.carbon_measurement_id",
                        "context.material_scope",
                    ],
                    "code": "verified_measurement_required",
                },
            )
        try:
            measurement = await self._measurement.get_measurement(
                company_id=context.company_id,
                measurement_id=resolved_id,
            )
        except MeasurementServiceError as error:
            return None, self._measurement_failure(error)
        mismatch = self._measurement_scope_failure(context, measurement)
        return (None, mismatch) if mismatch is not None else (measurement, None)

    async def _scenario(
        self,
        context: AgentToolContext,
        scenario_id: UUID,
    ) -> tuple[ProcurementScenarioResult | None, AgentToolResult | None]:
        if self._procurement is None:
            return None, AgentToolResult.unsupported("load_supplier_candidates")
        try:
            scenario = await self._procurement.get_scenario(
                company_id=context.company_id,
                scenario_id=scenario_id,
            )
        except ProcurementError as error:
            return None, self._procurement_failure(error)
        if context.site_id is not None and scenario.site_id != context.site_id:
            return None, self._stale_scenario_scope()
        if (
            context.reporting_period_id is not None
            and scenario.reporting_period_id != context.reporting_period_id
        ):
            return None, self._stale_scenario_scope()
        measurement_id = self._measurement_id(context)
        if measurement_id is not None and scenario.carbon_measurement_id != measurement_id:
            return None, self._stale_scenario_scope()
        if (
            context.current_product_id is not None
            and scenario.current_product.id != context.current_product_id
        ):
            return None, self._stale_scenario_scope()
        if context.method_definition_ids and scenario.method.id not in context.method_definition_ids:
            return None, self._stale_scenario_scope()
        if not self._scenario_constraints_match(context, scenario):
            return None, self._stale_scenario_scope()
        return scenario, None

    async def _dispatch_scenario(
        self,
        context: AgentToolContext,
        scenario_id: UUID,
        *,
        forecast_id: UUID | None = None,
        method_definition_id: UUID | None = None,
    ) -> tuple[DispatchScenarioView | None, AgentToolResult | None]:
        if self._dispatch is None:
            return None, AgentToolResult.unsupported("optimize_dispatch_window")
        try:
            scenario = await self._dispatch.get_scenario(
                company_id=context.company_id,
                scenario_id=scenario_id,
            )
        except DispatchError as error:
            return None, self._dispatch_failure(error)

        flexible_load_id = self._entity_id(context, "flexible_load")
        policy_definition_id = self._entity_id(context, "policy_definition")
        frozen_forecast_id = self._entity_id(context, "grid_forecast")
        if (
            (context.site_id is not None and scenario.site_id != context.site_id)
            or (
                flexible_load_id is not None
                and scenario.flexible_load.id != flexible_load_id
            )
            or (
                policy_definition_id is not None
                and scenario.policy_definition_id != policy_definition_id
            )
            or (
                forecast_id is not None
                and scenario.forecast_source_document_id != forecast_id
            )
            or (
                frozen_forecast_id is not None
                and scenario.forecast_source_document_id != frozen_forecast_id
            )
            or (
                method_definition_id is not None
                and scenario.method.id != method_definition_id
            )
            or (
                context.modules == ("dispatch",)
                and context.method_definition_ids
                and scenario.method.id not in context.method_definition_ids
            )
            or not self._dispatch_constraints_match(context, scenario)
        ):
            return None, self._stale_dispatch_scope()
        return scenario, None

    async def _resolved_workflow_inputs(
        self,
        context: AgentToolContext,
    ) -> tuple[ResolvedWorkflowInputs | None, AgentToolResult | None]:
        if self._workflow_resolver is None:
            return None, None
        if not ({"measurement", "procurement"} & set(context.modules)):
            return None, None
        if context.site_id is None or context.reporting_period_id is None:
            return None, None
        activity_record_ids = [
            entity.entity_id
            for entity in context.entities
            if entity.entity_type == "activity_record"
        ]
        if (
            not context.material_scope
            and self._measurement_id(context) is None
            and not activity_record_ids
        ):
            return None, None

        procurement = "procurement" in context.modules
        metric_keys = [CARBON_METRIC_KEY]
        if procurement:
            metric_keys.extend(sorted(PROCUREMENT_METRIC_KEYS))
        request = AgentContextRequest(
            company_id=context.company_id,
            actor_id=context.actor_id,
            site_id=context.site_id,
            reporting_period_id=context.reporting_period_id,
            carbon_measurement_id=self._measurement_id(context),
            current_product_id=context.current_product_id,
            method_definition_id=(
                context.method_definition_ids[0] if context.method_definition_ids else None
            ),
            activity_record_ids=activity_record_ids,
            metric_keys=metric_keys,
            material_scope=list(context.material_scope),
        )
        try:
            resolved = await self._workflow_resolver.resolve(
                context=request,
                workflow="procurement" if procurement else "measurement",
            )
        except WorkflowStop as error:
            return None, self._workflow_failure(error)
        except SQLAlchemyError:
            return None, AgentToolResult(
                status="failed",
                code="context_resolution_unavailable",
                message="Deterministic workflow context is temporarily unavailable.",
            )
        requested_activity_ids = {
            entity.entity_id
            for entity in context.entities
            if entity.entity_type == "activity_record"
        }
        if requested_activity_ids and requested_activity_ids != {resolved.activity.id}:
            return None, AgentToolResult(
                status="stale",
                code="resolved_activity_scope_mismatch",
                message=(
                    "The resolved activity does not exactly match the frozen activity scope."
                ),
            )
        return resolved, None

    async def _assurance_draft(
        self,
        context: AgentToolContext,
        draft_id: UUID | None,
    ) -> tuple[DisclosureDraftView | None, AgentToolResult | None]:
        if self._assurance is None:
            return None, AgentToolResult.unsupported("map_standard_requirement")
        if draft_id is None:
            return None, self._clarification(
                code="assurance_draft_required",
                field="context.disclosure_draft_id",
                message="A persisted disclosure draft is required for Assurance tools.",
            )
        try:
            draft = await self._assurance.get_draft(
                company_id=context.company_id,
                draft_id=draft_id,
            )
        except AssuranceError as error:
            return None, self._assurance_failure(error)
        if context.site_id is not None and draft.site_id != context.site_id:
            return None, self._stale_assurance_scope()
        if (
            context.reporting_period_id is not None
            and draft.reporting_period_id != context.reporting_period_id
        ):
            return None, self._stale_assurance_scope()
        measurement_id = self._measurement_id(context)
        if measurement_id is not None and draft.measurement_id != measurement_id:
            return None, self._stale_assurance_scope()
        standard_id = self._entity_id(context, "standard")
        if standard_id is not None and draft.standard.id != standard_id:
            return None, self._stale_assurance_scope()
        frozen_requirement_ids = {
            entity.entity_id
            for entity in context.entities
            if entity.entity_type == "disclosure_requirement"
        }
        if frozen_requirement_ids:
            try:
                persisted_requirement_ids = {
                    UUID(str(value))
                    for value in draft.validation_summary.get("requirement_ids", [])
                }
            except (TypeError, ValueError):
                return None, self._stale_assurance_scope()
            if frozen_requirement_ids != persisted_requirement_ids:
                return None, self._stale_assurance_scope()
        frozen_evidence_ids = {
            entity.entity_id
            for entity in context.entities
            if entity.entity_type == "evidence_item"
        }
        if frozen_evidence_ids:
            cited_evidence_ids = {
                citation.evidence_item_id
                for claim in draft.claims
                for citation in claim.citations
                if citation.evidence_item_id is not None
            }
            # Explicit evidence IDs are an allowlist. A draft may need only a
            # subset, but it must never silently bind a source outside the
            # caller's frozen evidence scope.
            if not cited_evidence_ids.issubset(frozen_evidence_ids):
                return None, self._stale_assurance_scope()
        return draft, None

    async def _draft_for_claim(
        self,
        context: AgentToolContext,
        claim_id: UUID,
    ) -> tuple[DisclosureDraftView | None, AgentToolResult | None]:
        draft, failure = await self._assurance_draft(
            context,
            self._assurance_draft_id(context),
        )
        if failure is not None:
            return None, failure
        assert draft is not None
        if claim_id not in {item.id for item in draft.claims}:
            return None, AgentToolResult(
                status="stale",
                code="assurance_claim_set_mismatch",
                message="The selected claim is outside the frozen disclosure draft.",
            )
        return draft, None

    def _forecast_provider(self, source_mode: str) -> ElectricityMapsProvider | None:
        return (
            self._fixture_grid_provider
            if source_mode == "fixture"
            else self._live_grid_provider
        )

    @staticmethod
    def _scenario_constraints_match(
        context: AgentToolContext,
        scenario: ProcurementScenarioResult,
    ) -> bool:
        expected = {
            "max_cost_increase_pct": scenario.constraints.max_cost_increase_pct,
            "max_lead_time_days": scenario.constraints.max_lead_time_days,
            "minimum_circularity_score": scenario.constraints.minimum_circularity_score,
        }
        for key, scenario_value in expected.items():
            requested_value = context.constraints.get(key)
            if requested_value is None:
                continue
            try:
                if Decimal(str(requested_value)) != Decimal(str(scenario_value)):
                    return False
            except (InvalidOperation, TypeError, ValueError):
                return False

        if not context.material_scope:
            return True
        requested_materials = {
            CarbonMeshAgentToolServicePort._normalized_scope(value)
            for value in context.material_scope
        }
        scenario_materials = scenario.constraints.material.allowed_material_codes or [
            scenario.current_product.material_code
        ]
        return {
            CarbonMeshAgentToolServicePort._normalized_scope(value)
            for value in scenario_materials
        }.issubset(requested_materials)

    @classmethod
    def _dispatch_constraints_match(
        cls,
        context: AgentToolContext,
        scenario: DispatchScenarioView,
    ) -> bool:
        prefix = "dispatch."
        raw_constraints = context.constraints
        constraint_items = (
            raw_constraints.items()
            if isinstance(raw_constraints, dict)
            else ((item.key, item.value) for item in raw_constraints)
        )
        requested = {
            key.removeprefix(prefix): value
            for key, value in constraint_items
            if key.startswith(prefix)
        }
        if not requested:
            return True

        try:
            for key, actual in (
                ("window_start", scenario.window_start),
                ("window_end", scenario.window_end),
            ):
                if key not in requested:
                    continue
                raw = requested[key]
                parsed = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
                if cls._as_utc(parsed) != cls._as_utc(actual):
                    return False

            if (
                "duration_minutes" in requested
                and int(str(requested["duration_minutes"]))
                != scenario.flexible_load.duration_minutes
            ):
                return False
            if (
                "max_delay_minutes" in requested
                and int(str(requested["max_delay_minutes"]))
                != scenario.constraints.maximum_delay_minutes
            ):
                return False
            if "maximum_power_kw" in requested:
                maximum_power = scenario.flexible_load.maximum_power_kw
                if maximum_power is None or Decimal(
                    str(requested["maximum_power_kw"])
                ) != Decimal(str(maximum_power)):
                    return False
            if "blackout_constraint_ids" in requested:
                raw_ids = requested["blackout_constraint_ids"]
                if isinstance(raw_ids, str):
                    raw_ids = json.loads(raw_ids)
                if not isinstance(raw_ids, list):
                    return False
                expected_ids = {UUID(str(value)) for value in raw_ids}
                actual_ids = {
                    item.id
                    for item in scenario.constraints.source_constraints
                    if item.is_hard and item.constraint_type == "blackout"
                }
                if expected_ids != actual_ids:
                    return False
        except (InvalidOperation, TypeError, ValueError, json.JSONDecodeError):
            return False
        return True

    @staticmethod
    def _normalized_scope(value: str) -> str:
        return "".join(character for character in value.casefold() if character.isalnum())

    @staticmethod
    def _normalized_reference(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _measurement_outputs(measurement: MeasurementDetail) -> dict[str, object]:
        return {
            "carbon_measurement_id": str(measurement.id),
            "measurement_id": str(measurement.id),
            "activity_record_ids": ([str(item.activity_record_id) for item in measurement.inputs]
                                    if len(measurement.inputs) <= 100 else []),
            "canonical_unit": "kWh" if measurement.metric_key == "emissions.scope2.location_based" else "kg",
            "calculation_run_id": str(measurement.calculation_run.id),
            "method_definition_id": str(measurement.calculation_run.method_definition_id),
            "metric_key": measurement.metric_key,
        }

    @staticmethod
    def _factor_set_hash(measurement: MeasurementDetail) -> str:
        sources = sorted((str(item.activity_record_id), str(item.emission_factor_id),
                          str(item.grid_intensity_point_id), item.output_hash)
                         for item in measurement.calculations)
        return hashlib.sha256(json.dumps(sources, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _scenario_outputs(scenario: ProcurementScenarioResult) -> dict[str, object]:
        recommendation = scenario.selected_recommendation
        selected_score_id = None
        if recommendation is not None:
            selected_score_id = next(
                (
                    item.score_id
                    for item in scenario.alternatives
                    if item.product.id == recommendation.recommended_product_id
                ),
                None,
            )
        return {
            "scenario_id": str(scenario.id),
            "procurement_scenario_id": str(scenario.id),
            "carbon_measurement_id": str(scenario.carbon_measurement_id),
            "current_product_id": str(scenario.current_product.id),
            "method_definition_id": str(scenario.method.id),
            "supplier_product_ids": [str(item.product.id) for item in scenario.alternatives],
            "supplier_score_ids": [str(item.score_id) for item in scenario.alternatives],
            "selected_supplier_score_id": (
                str(selected_score_id) if selected_score_id is not None else None
            ),
            "recommendation_id": str(recommendation.id) if recommendation is not None else None,
        }

    @staticmethod
    def _dispatch_scenario_outputs(scenario: DispatchScenarioView) -> dict[str, object]:
        return {
            "dispatch_scenario_id": str(scenario.id),
            "forecast_id": str(scenario.forecast_source_document_id),
            "forecast_source_document_id": str(scenario.forecast_source_document_id),
            "dispatch_method_definition_id": str(scenario.method.id),
            "flexible_load_id": str(scenario.flexible_load.id),
        }

    @staticmethod
    def _measurement_fact(
        measurement: MeasurementDetail,
    ) -> tuple[AgentToolFact | None, AgentToolResult | None]:
        if measurement.facts.ledger_event_id is None:
            return None, AgentToolResult(
                status="validation_failed",
                code="measurement_ledger_event_missing",
                message="The verified measurement has no ledger event and cannot be returned.",
            )
        return (
            AgentToolFact(
                fact_id=measurement.facts.fact_id,
                metric_key=measurement.metric_key,
                ledger_event_id=measurement.facts.ledger_event_id,
                display_value=f"{measurement.value_kgco2e} {measurement.unit}",
            ),
            None,
        )

    @staticmethod
    def _measurement_scope_failure(
        context: AgentToolContext,
        measurement: MeasurementDetail,
    ) -> AgentToolResult | None:
        if context.site_id is not None and measurement.site_id != context.site_id:
            return CarbonMeshAgentToolServicePort._stale_measurement_scope()
        if (
            context.reporting_period_id is not None
            and measurement.reporting_period_id != context.reporting_period_id
        ):
            return CarbonMeshAgentToolServicePort._stale_measurement_scope()
        if (
            context.modules == ("measurement",)
            and context.method_definition_ids
            and measurement.calculation_run.method_definition_id
            not in context.method_definition_ids
        ):
            return CarbonMeshAgentToolServicePort._stale_measurement_scope()
        return None

    @staticmethod
    def _measurement_failure(error: MeasurementServiceError) -> AgentToolResult:
        status = {
            "no_data": "no_data",
            "needs_clarification": "needs_clarification",
            "unsupported": "unsupported",
            "validation_error": "validation_failed",
            "failed_validation": "validation_failed",
        }[error.terminal_state]
        data: dict[str, object] = {}
        if status == "needs_clarification":
            fields = list(error.field_details or {})
            data = {
                "required_fields": fields or ["context.carbon_measurement_id"],
                "code": error.code,
            }
        return AgentToolResult(
            status=cast(AgentToolStatus, status),
            code=error.code,
            message=error.message,
            data=data,
        )

    @staticmethod
    def _procurement_failure(error: ProcurementError) -> AgentToolResult:
        status = "no_data" if error.status_code == 404 else "validation_failed"
        if error.status_code >= 500:
            status = "failed"
        return AgentToolResult(
            status=cast(AgentToolStatus, status),
            code=error.code,
            message=error.message,
        )

    @staticmethod
    def _assurance_failure(error: AssuranceError) -> AgentToolResult:
        if error.status_code == 404:
            status: AgentToolStatus = "no_data"
        elif error.code == "assurance_stale" or "stale" in error.code:
            status = "stale"
        elif error.retryable or error.status_code >= 500:
            status = "failed"
        else:
            status = "validation_failed"
        return AgentToolResult(
            status=status,
            code=error.code,
            message=error.message,
        )

    @staticmethod
    def _dispatch_failure(error: DispatchError) -> AgentToolResult:
        if error.retryable:
            status: AgentToolStatus = "provider_unavailable"
        elif error.status_code == 404:
            status = "no_data"
        elif error.status_code == 409 or "stale" in error.code:
            status = "stale"
        elif error.status_code >= 500:
            status = "failed"
        else:
            status = "validation_failed"
        return AgentToolResult(status=status, code=error.code, message=error.message)

    @staticmethod
    def _integration_failure(error: IntegrationServiceError) -> AgentToolResult:
        if error.retryable or error.status_code in {401, 503}:
            status: AgentToolStatus = "provider_unavailable"
        elif error.status_code == 404:
            status = "no_data"
        elif error.status_code >= 500:
            status = "validation_failed"
        else:
            status = "validation_failed"
        return AgentToolResult(status=status, code=error.code, message=error.message)

    @staticmethod
    def _workflow_failure(error: WorkflowStop) -> AgentToolResult:
        status = {
            "no_data": "no_data",
            "needs_clarification": "needs_clarification",
            "unsupported": "unsupported",
            "failed_validation": "validation_failed",
            "validation_failed": "validation_failed",
        }.get(error.terminal_state, "validation_failed")
        data: dict[str, object] = {}
        if status == "needs_clarification":
            data = {
                "required_fields": error.missing_fields,
                "code": error.code,
            }
        return AgentToolResult(
            status=cast(AgentToolStatus, status),
            code=error.code,
            message=error.message,
            data=data,
        )

    @staticmethod
    def _entity_id(context: AgentToolContext, entity_type: str) -> UUID | None:
        return next(
            (
                entity.entity_id
                for entity in context.entities
                if entity.entity_type == entity_type
            ),
            None,
        )

    @classmethod
    def _assurance_draft_id(cls, context: AgentToolContext) -> UUID | None:
        return cls._entity_id(context, "disclosure_draft")

    @classmethod
    def _measurement_id(cls, context: AgentToolContext) -> UUID | None:
        return context.carbon_measurement_id or cls._entity_id(context, "carbon_measurement")

    @staticmethod
    def _invalid_arguments(tool_name: AgentToolName) -> AgentToolResult:
        return AgentToolResult(
            status="validation_failed",
            code="tool_argument_type_mismatch",
            message=f"The {tool_name} handler received an incompatible typed payload.",
        )

    @staticmethod
    def _resolved_entity(entity_type: str, entity_id: UUID) -> AgentToolResult:
        return AgentToolResult(
            status="success",
            data={
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                f"{entity_type}_id": str(entity_id),
            },
            rows=1,
        )

    @classmethod
    def _unique_entity_result(
        cls,
        entity_type: str,
        identifiers: list[UUID],
    ) -> AgentToolResult:
        unique = sorted(set(identifiers), key=str)
        if len(unique) == 1:
            return cls._resolved_entity(entity_type, unique[0])
        if not unique:
            return AgentToolResult(
                status="no_data",
                code="entity_not_found",
                message=f"No active {entity_type} matches the requested reference.",
            )
        return cls._clarification(
            code="entity_reference_ambiguous",
            field=f"context.{entity_type}_id",
            message=f"More than one {entity_type} matches the requested reference.",
        )

    @staticmethod
    def _entity_not_resolved(entity_type: str) -> AgentToolResult:
        return AgentToolResult(
            status="needs_clarification",
            code="entity_reference_unresolved",
            message=f"The {entity_type} reference cannot be resolved uniquely.",
            data={
                "required_fields": [f"context.{entity_type}_id"],
                "code": "entity_reference_unresolved",
            },
        )

    @staticmethod
    def _clarification(*, code: str, field: str, message: str) -> AgentToolResult:
        return AgentToolResult(
            status="needs_clarification",
            code=code,
            message=message,
            data={"required_fields": [field], "code": code},
        )

    @staticmethod
    def _missing_approval_preview() -> AgentToolResult:
        return AgentToolResult(
            status="validation_failed",
            code="approval_preview_missing",
            message="The consequential target has no persisted approval preview.",
        )

    @staticmethod
    def _stale_approval_hash() -> AgentToolResult:
        return AgentToolResult(
            status="stale",
            code="approval_preview_hash_mismatch",
            message="The persisted target no longer matches the requested preview.",
        )

    @staticmethod
    def _stale_measurement_scope() -> AgentToolResult:
        return AgentToolResult(
            status="stale",
            code="measurement_context_mismatch",
            message=(
                "The verified measurement does not match the frozen site, period, "
                "or method scope."
            ),
        )

    @staticmethod
    def _stale_measurement_target() -> AgentToolResult:
        return AgentToolResult(
            status="stale",
            code="measurement_target_mismatch",
            message="The confidence target does not match the verified measurement.",
        )

    @staticmethod
    def _stale_scenario_scope() -> AgentToolResult:
        return AgentToolResult(
            status="stale",
            code="procurement_scenario_context_mismatch",
            message="The Procurement scenario does not match the frozen context.",
        )

    @staticmethod
    def _stale_assurance_scope() -> AgentToolResult:
        return AgentToolResult(
            status="stale",
            code="assurance_draft_context_mismatch",
            message="The disclosure draft does not match the frozen context.",
        )

    @staticmethod
    def _stale_dispatch_scope() -> AgentToolResult:
        return AgentToolResult(
            status="stale",
            code="dispatch_scenario_context_mismatch",
            message="The Dispatch scenario does not match the frozen context.",
        )


__all__ = ["CarbonMeshAgentToolServicePort"]
