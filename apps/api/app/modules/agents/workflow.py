from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import ActivityRecord, CarbonMeasurement
from app.db.models.procurement import SupplierProduct
from app.db.models.semantic import MethodDefinition
from app.modules.agents.repository import AgentWorkflowRepository
from app.modules.agents.schemas import (
    AgentContextRequest,
    AgentFact,
    AgentRecommendation,
    ApprovalRequirement,
    FrozenContextEnvelope,
    ResolvedWorkflowContext,
    TerminalState,
    Workflow,
)
from app.modules.procurement.errors import ProcurementError
from app.modules.procurement.schemas import CreateScenarioRequest, ProcurementScenarioResult
from app.modules.procurement.service import ProcurementService

MAX_RESOLUTION_ROWS = 101
MAX_SCORING_METHOD_ROWS = 2
CARBON_METRIC_KEY = "emissions.scope3.category1"
PROCUREMENT_METRIC_KEYS = frozenset(
    {
        "procurement.projected_avoided_emissions",
        "procurement.cost_delta_pct",
    }
)


class WorkflowStop(RuntimeError):
    """A safe, expected domain stop that should be persisted as a terminal run."""

    def __init__(
        self,
        terminal_state: TerminalState,
        message: str,
        *,
        missing_fields: list[str] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.terminal_state = terminal_state
        self.message = message
        self.missing_fields = missing_fields or []
        self.code = code or terminal_state


class WorkflowExecutionError(RuntimeError):
    """A sanitized infrastructure or unexpected workflow failure."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ResolvedWorkflowInputs:
    context: ResolvedWorkflowContext
    measurement: CarbonMeasurement
    activity: ActivityRecord
    current_product: SupplierProduct
    method: MethodDefinition | None
    rows_resolved: int

    def measurement_fact(self) -> AgentFact:
        if self.measurement.ledger_event_id is None:
            raise WorkflowStop(
                "failed_validation",
                "The verified measurement has no ledger event and cannot be returned as a fact.",
                missing_fields=["data.measurement.ledger_event_id"],
                code="measurement_missing_ledger_event",
            )
        return AgentFact(
            fact_id=self.measurement.id,
            metric_key=CARBON_METRIC_KEY,
            display_value=f"{self.measurement.value_kgco2e} {self.measurement.unit}",
            ledger_event_id=self.measurement.ledger_event_id,
        )


@dataclass(frozen=True, slots=True)
class WorkflowOutcome:
    terminal_state: TerminalState
    message: str
    facts: list[AgentFact]
    recommendation: AgentRecommendation | None = None
    approval_requirement: ApprovalRequirement = field(default_factory=ApprovalRequirement)
    rows_processed: int = 0
    tool_operations: tuple[WorkflowToolOperation, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowToolOperation:
    tool_id: str
    tool_name: str
    row_count: int


class AgentWorkflowExecutor:
    """Resolve frozen inputs and invoke only existing deterministic domain services."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        repository: AgentWorkflowRepository | None = None,
    ) -> None:
        self._repository = repository or AgentWorkflowRepository(session)
        self._procurement = ProcurementService(session)

    async def resolve(
        self,
        *,
        context: AgentContextRequest,
        workflow: Workflow,
    ) -> ResolvedWorkflowInputs:
        supported_metrics = {CARBON_METRIC_KEY, *PROCUREMENT_METRIC_KEYS}
        unsupported_metrics = set(context.metric_keys) - supported_metrics
        if unsupported_metrics:
            raise WorkflowStop(
                "unsupported",
                "The explicit metric scope contains metrics unsupported by this bounded workflow.",
                missing_fields=["context.metric_keys"],
                code="metric_scope_unsupported",
            )
        required_metrics = {CARBON_METRIC_KEY}
        if workflow in {"procurement", "cross_module"}:
            required_metrics.update(PROCUREMENT_METRIC_KEYS)
        missing_metrics = required_metrics - set(context.metric_keys)
        if context.metric_keys and missing_metrics:
            raise WorkflowStop(
                "failed_validation",
                (
                    "The explicit metric scope omits required workflow metrics: "
                    + ", ".join(sorted(missing_metrics))
                    + "."
                ),
                missing_fields=[
                    f"context.metric_keys[{metric}]" for metric in sorted(missing_metrics)
                ],
                code="workflow_metric_scope_incomplete",
            )
        if workflow in {"procurement", "cross_module"} and context.supplier_product_ids:
            raise WorkflowStop(
                "unsupported",
                (
                    "Supplier-product allowlists are not yet supported by the deterministic "
                    "Procurement service; no products were assessed outside the requested scope."
                ),
                missing_fields=["context.supplier_product_ids"],
                code="supplier_product_scope_unsupported",
            )
        if context.site_id is None or context.reporting_period_id is None:
            raise WorkflowStop(
                "needs_clarification",
                "Site and reporting period are required before workflow inputs can be resolved.",
                missing_fields=["context.site_id", "context.reporting_period_id"],
            )

        rows = await self._repository.list_measurement_inputs(
            company_id=context.company_id,
            site_id=context.site_id,
            reporting_period_id=context.reporting_period_id,
            metric_key=CARBON_METRIC_KEY,
            carbon_measurement_id=context.carbon_measurement_id,
            current_product_id=context.current_product_id,
            limit=MAX_RESOLUTION_ROWS,
        )
        if context.material_scope:
            normalized_scope = {_normalize_scope(value) for value in context.material_scope}
            rows = [
                row
                for row in rows
                if _normalize_scope(row.activity.material_code) in normalized_scope
            ]
        if not rows:
            raise WorkflowStop(
                "no_data",
                "No verified measurement and valid purchased-material activity match the frozen context.",
                missing_fields=["data.verified_measurement", "data.valid_activity_record"],
                code="verified_measurement_not_found",
            )
        if len(rows) > 1:
            raise WorkflowStop(
                "needs_clarification",
                "More than one verified measurement/activity input matches the frozen context.",
                missing_fields=[
                    "context.carbon_measurement_id",
                    "context.current_product_id",
                    "context.material_scope",
                ],
                code="ambiguous_measurement_context",
            )

        resolved_input = rows[0]
        measurement = resolved_input.measurement
        activity = resolved_input.activity
        product = resolved_input.product
        method: MethodDefinition | None = None
        rows_resolved = 3
        if workflow in {"procurement", "cross_module"}:
            period = await self._repository.get_reporting_period(
                company_id=context.company_id,
                reporting_period_id=context.reporting_period_id,
            )
            if period is None:  # guarded by T01, retained for direct executor use
                raise WorkflowStop(
                    "no_data",
                    "The reporting period is no longer available.",
                    missing_fields=["data.reporting_period"],
                )
            methods = await self._repository.list_scoring_methods(
                company_id=context.company_id,
                period_start=period.start_date,
                period_end=period.end_date,
                method_definition_id=context.method_definition_id,
                limit=MAX_SCORING_METHOD_ROWS,
            )
            if not methods:
                raise WorkflowStop(
                    "no_data",
                    "No active supplier-scoring method matches the reporting period.",
                    missing_fields=["data.supplier_scoring_method"],
                    code="scoring_method_not_found",
                )
            if len(methods) > 1:
                raise WorkflowStop(
                    "needs_clarification",
                    "More than one supplier-scoring method matches the reporting period.",
                    missing_fields=["context.method_definition_id"],
                    code="ambiguous_scoring_method",
                )
            method = methods[0]
            rows_resolved += 2

        resolved_context = ResolvedWorkflowContext(
            carbon_measurement_id=measurement.id,
            activity_record_id=activity.id,
            current_product_id=product.id,
            method_definition_id=method.id if method else None,
            quantity=activity.normalized_quantity,
            quantity_unit=activity.normalized_unit,
            current_unit_cost=(
                activity.unit_cost if activity.unit_cost is not None else product.unit_cost
            ),
            currency=activity.currency or product.currency,
        )
        return ResolvedWorkflowInputs(
            context=resolved_context,
            measurement=measurement,
            activity=activity,
            current_product=product,
            method=method,
            rows_resolved=rows_resolved,
        )

    async def execute_procurement(
        self,
        *,
        inputs: ResolvedWorkflowInputs,
        context: AgentContextRequest,
        frozen_context: FrozenContextEnvelope,
        agent_run_id: UUID,
    ) -> WorkflowOutcome:
        if context.supplier_product_ids:
            raise WorkflowStop(
                "unsupported",
                (
                    "Supplier-product allowlists are not yet supported by the deterministic "
                    "Procurement service; no products were assessed outside the requested scope."
                ),
                missing_fields=["context.supplier_product_ids"],
                code="supplier_product_scope_unsupported",
            )
        if inputs.method is None:
            raise WorkflowStop(
                "failed_validation",
                "A resolved supplier-scoring method is required for Procurement.",
                missing_fields=["data.supplier_scoring_method"],
            )
        constraints = context.constraints
        if (
            constraints.max_cost_increase_pct is None
            or constraints.max_lead_time_days is None
            or constraints.minimum_circularity_score is None
        ):
            raise WorkflowStop(
                "needs_clarification",
                "Every hard Procurement constraint must be explicit.",
                missing_fields=[
                    "context.constraints.max_cost_increase_pct",
                    "context.constraints.max_lead_time_days",
                    "context.constraints.minimum_circularity_score",
                ],
            )

        material_constraints = {
            "allowed_material_codes": [inputs.activity.material_code],
        }
        request = CreateScenarioRequest(
            company_id=context.company_id,
            site_id=context.site_id,
            reporting_period_id=context.reporting_period_id,
            current_product_id=inputs.current_product.id,
            carbon_measurement_id=inputs.measurement.id,
            method_definition_id=inputs.method.id,
            requested_by=context.actor_id,
            agent_run_id=agent_run_id,
            quantity=inputs.activity.normalized_quantity,
            quantity_unit=inputs.activity.normalized_unit,
            current_unit_cost=(
                inputs.activity.unit_cost
                if inputs.activity.unit_cost is not None
                else inputs.current_product.unit_cost
            ),
            currency=inputs.activity.currency or inputs.current_product.currency,
            max_cost_increase_pct=constraints.max_cost_increase_pct,
            max_lead_time_days=constraints.max_lead_time_days,
            minimum_circularity_score=constraints.minimum_circularity_score,
            material_constraints=material_constraints,
            idempotency_key=f"agent-{frozen_context.analysis_signature}",
        )
        try:
            scenario = await self._procurement.create_scenario(request)
            outcome = await self._outcome(inputs, scenario)
            alternative_count = len(scenario.alternatives)
            operations = [
                WorkflowToolOperation(
                    tool_id="T09",
                    tool_name="list_supplier_alternatives",
                    row_count=alternative_count,
                )
            ]
            if scenario.agent_run_id == agent_run_id:
                operations.extend(
                    (
                        WorkflowToolOperation(
                            tool_id="T10",
                            tool_name="score_supplier_products",
                            row_count=alternative_count,
                        ),
                        WorkflowToolOperation(
                            tool_id="T11",
                            tool_name="calculate_procurement_impact",
                            row_count=alternative_count,
                        ),
                    )
                )
                if outcome.recommendation is not None:
                    operations.append(
                        WorkflowToolOperation(
                            tool_id="T12",
                            tool_name="create_approval_preview",
                            row_count=1,
                        )
                    )
            return replace(outcome, tool_operations=tuple(operations))
        except ProcurementError as error:
            if error.status_code >= 500 or error.retryable:
                raise WorkflowExecutionError(
                    code=error.code,
                    message="Deterministic Procurement execution is temporarily unavailable.",
                ) from error
            state: TerminalState = "no_data" if error.status_code == 404 else "failed_validation"
            raise WorkflowStop(
                state,
                error.message,
                missing_fields=[
                    str(detail.get("field"))
                    for detail in error.field_details
                    if detail.get("field")
                ],
                code=error.code,
            ) from error

    async def _outcome(
        self,
        inputs: ResolvedWorkflowInputs,
        scenario: ProcurementScenarioResult,
    ) -> WorkflowOutcome:
        measurement_fact = inputs.measurement_fact()
        selected = scenario.selected_recommendation
        if scenario.terminal_state == "no_feasible_option" or selected is None:
            return WorkflowOutcome(
                terminal_state="no_feasible_option",
                message="No supplier product satisfies every frozen hard constraint.",
                facts=[measurement_fact],
                rows_processed=len(scenario.alternatives),
            )

        detail = await self._procurement.get_recommendation(
            company_id=scenario.company_id,
            recommendation_id=selected.id,
        )
        if detail.ledger_event_id is None:
            raise WorkflowStop(
                "failed_validation",
                "The recommendation has no ledger event and cannot be returned as a fact.",
                missing_fields=["data.recommendation.ledger_event_id"],
            )
        if detail.approval is None:
            raise WorkflowStop(
                "failed_validation",
                "The recommendation has no approval preview.",
                missing_fields=["data.recommendation.approval"],
            )

        avoided_fact = AgentFact(
            fact_id=detail.id,
            metric_key="procurement.projected_avoided_emissions",
            display_value=f"{detail.avoided_kgco2e} kgCO2e",
            ledger_event_id=detail.ledger_event_id,
        )
        cost_fact = AgentFact(
            fact_id=detail.supplier_score.score_id,
            metric_key="procurement.cost_delta_pct",
            display_value=f"{detail.cost_delta_pct}%",
            ledger_event_id=detail.ledger_event_id,
        )
        recommendation = AgentRecommendation(
            scenario_id=detail.scenario_id,
            recommendation_id=detail.id,
            recommended_product_id=detail.recommended_product.id,
            status=detail.status,
            projected_footprint_kgco2e=detail.projected_footprint_kgco2e,
            avoided_kgco2e=detail.avoided_kgco2e,
            reduction_pct=detail.reduction_pct,
            cost_delta_pct=detail.cost_delta_pct,
            lead_time_delta_days=detail.lead_time_delta_days,
            payload_hash=detail.payload_hash,
            analysis_signature=detail.analysis_signature,
            ledger_event_id=detail.ledger_event_id,
        )
        approval = ApprovalRequirement(
            required=detail.approval.status == "pending",
            approval_id=detail.approval.id,
            recommendation_id=detail.id,
            preview_hash=detail.approval.preview_hash,
        )
        return WorkflowOutcome(
            terminal_state="completed",
            message=(
                "Verified Measurement facts were used to create or reuse a deterministic "
                "Procurement recommendation with an exact approval preview."
            ),
            facts=[measurement_fact, avoided_fact, cost_fact],
            recommendation=recommendation,
            approval_requirement=approval,
            rows_processed=len(scenario.alternatives) + 1,
        )


def _normalize_scope(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())
