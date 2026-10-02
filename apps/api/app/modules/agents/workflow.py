from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import ActivityRecord, CarbonMeasurement
from app.db.models.procurement import SupplierProduct
from app.db.models.semantic import MethodDefinition
from app.modules.agents.repository import AgentWorkflowRepository
from app.modules.agents.schemas import (
    AgentContextRequest,
    TerminalState,
    Workflow,
)

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


@dataclass(frozen=True, slots=True)
class ResolvedWorkflowInputs:
    measurement: CarbonMeasurement
    activity: ActivityRecord
    current_product: SupplierProduct
    method: MethodDefinition | None
    rows_resolved: int


class AgentWorkflowExecutor:
    """Resolve bounded workflow inputs through repository methods."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        repository: AgentWorkflowRepository | None = None,
    ) -> None:
        self._repository = repository or AgentWorkflowRepository(session)

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

        return ResolvedWorkflowInputs(
            measurement=measurement,
            activity=activity,
            current_product=product,
            method=method,
            rows_resolved=rows_resolved,
        )


def _normalize_scope(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())
