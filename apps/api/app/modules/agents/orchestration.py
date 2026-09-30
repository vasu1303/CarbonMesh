from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final

from app.modules.agents.schemas import (
    AgentBudget,
    AgentContextRequest,
    AgentPlan,
    AgentPlanStep,
    FrozenContextEnvelope,
    ResolvedWorkflowContext,
    Workflow,
)

MEASUREMENT_TERMS: Final[tuple[str, ...]] = (
    "baseline",
    "carbon footprint",
    "emission factor",
    "emissions",
    "footprint",
    "kgco2e",
    "measure",
    "measurement",
    "scope 3",
)
PROCUREMENT_TERMS: Final[tuple[str, ...]] = (
    "alternative",
    "approval",
    "circularity",
    "cost",
    "lead time",
    "procure",
    "procurement",
    "recommend",
    "supplier",
)
OUT_OF_SCOPE_TERMS: Final[tuple[str, ...]] = (
    "assurance",
    "autonomous purchase",
    "dispatch",
    "equipment actuation",
    "grid forecast",
    "place an order",
    "purchase order",
)

DEFAULT_METRICS: Final[dict[str, list[str]]] = {
    "measurement": ["emissions.scope3.category1"],
    "procurement": [
        "emissions.scope3.category1",
        "procurement.projected_avoided_emissions",
        "procurement.cost_delta_pct",
    ],
    "cross_module": [
        "emissions.scope3.category1",
        "procurement.projected_avoided_emissions",
        "procurement.cost_delta_pct",
    ],
    "unsupported": [],
}


@dataclass(frozen=True, slots=True)
class Classification:
    workflow: Workflow
    matched_terms: tuple[str, ...]
    unsupported_reason: str | None = None


class BudgetExhaustedError(RuntimeError):
    """Raised before a bounded run would exceed an orchestration budget."""


@dataclass(slots=True)
class BudgetCounter:
    limits: AgentBudget
    model_calls: int = 0
    tool_calls: int = 0
    repairs: int = 0

    def consume_tool(self) -> None:
        if self.tool_calls >= self.limits.max_tool_calls:
            raise BudgetExhaustedError("tool-call budget exhausted")
        self.tool_calls += 1


def _contains_term(query: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", query) is not None


def classify_workflow(query: str) -> Classification:
    """Classify only the two POC workflows using transparent, stable rules."""
    normalized = query.casefold()
    unsupported = tuple(term for term in OUT_OF_SCOPE_TERMS if _contains_term(normalized, term))
    if unsupported:
        return Classification(
            workflow="unsupported",
            matched_terms=unsupported,
            unsupported_reason=(
                "The request includes a Phase 2 or autonomous action that is outside the "
                "Measurement and Procurement POC boundary."
            ),
        )

    measurement = tuple(term for term in MEASUREMENT_TERMS if _contains_term(normalized, term))
    procurement = tuple(term for term in PROCUREMENT_TERMS if _contains_term(normalized, term))
    if measurement and procurement:
        return Classification("cross_module", measurement + procurement)
    if measurement:
        return Classification("measurement", measurement)
    if procurement:
        return Classification("procurement", procurement)
    return Classification(
        workflow="unsupported",
        matched_terms=(),
        unsupported_reason=(
            "The request could not be classified as Measurement, Procurement, or their "
            "connected workflow."
        ),
    )


def required_context_fields(
    workflow: Workflow,
    context: AgentContextRequest,
) -> list[str]:
    if workflow == "unsupported":
        return []

    missing: list[str] = []
    if context.site_id is None:
        missing.append("context.site_id")
    if context.reporting_period_id is None:
        missing.append("context.reporting_period_id")
    if workflow in {"procurement", "cross_module"}:
        if not context.material_scope:
            missing.append("context.material_scope")
        if context.constraints.max_cost_increase_pct is None:
            missing.append("context.constraints.max_cost_increase_pct")
        if context.constraints.max_lead_time_days is None:
            missing.append("context.constraints.max_lead_time_days")
        if context.constraints.minimum_circularity_score is None:
            missing.append("context.constraints.minimum_circularity_score")
    return missing


def build_frozen_context(
    *,
    request_context: AgentContextRequest,
    actor_role: str,
    query: str,
    workflow: Workflow,
    resolved: ResolvedWorkflowContext | None = None,
) -> FrozenContextEnvelope:
    metric_keys = request_context.metric_keys or DEFAULT_METRICS[workflow]
    unsigned = {
        "company_id": str(request_context.company_id),
        "site_id": str(request_context.site_id) if request_context.site_id else None,
        "reporting_period_id": (
            str(request_context.reporting_period_id)
            if request_context.reporting_period_id
            else None
        ),
        "carbon_measurement_id": (
            str(request_context.carbon_measurement_id)
            if request_context.carbon_measurement_id
            else None
        ),
        "current_product_id": (
            str(request_context.current_product_id) if request_context.current_product_id else None
        ),
        "method_definition_id": (
            str(request_context.method_definition_id)
            if request_context.method_definition_id
            else None
        ),
        "workflow": workflow,
        "metric_keys": metric_keys,
        "material_scope": request_context.material_scope,
        "supplier_product_ids": [str(value) for value in request_context.supplier_product_ids],
        "actor_id": str(request_context.actor_id),
        "actor_role": actor_role,
        "constraints": request_context.constraints.model_dump(mode="json"),
        "resolved": resolved.model_dump(mode="json") if resolved else None,
        "request_hash": hashlib.sha256(query.encode("utf-8")).hexdigest(),
    }
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return FrozenContextEnvelope.model_validate(
        {**unsigned, "analysis_signature": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
    )


def build_plan(
    workflow: Workflow,
    missing_fields: list[str],
    *,
    executed: bool = False,
) -> AgentPlan:
    if missing_fields or workflow == "unsupported":
        execution_status = "blocked"
    else:
        execution_status = "completed" if executed else "planned"
    steps = [
        AgentPlanStep(
            id="resolve_context",
            title="Freeze authorized context",
            responsibility="Validate tenant references and bind an immutable analysis signature.",
            status="completed",
            tool_ids=["T01"],
        )
    ]
    if workflow in {"measurement", "cross_module"}:
        steps.append(
            AgentPlanStep(
                id="measurement",
                title="Run deterministic measurement",
                responsibility=(
                    "Validate activity, select a versioned factor, calculate emissions, and "
                    "write traceable facts through typed Measurement services."
                ),
                status=execution_status,
                tool_ids=["T03"],
            )
        )
    if workflow in {"procurement", "cross_module"}:
        steps.append(
            AgentPlanStep(
                id="procurement",
                title="Run deterministic procurement assessment",
                responsibility=(
                    "Apply hard constraints, score feasible products, calculate impact, and "
                    "create an approval preview through typed Procurement services."
                ),
                status=execution_status,
                tool_ids=["T09", "T10", "T11", "T12"],
            )
        )
    return AgentPlan(
        workflow=workflow,
        steps=steps,
        requires_human_approval=workflow in {"procurement", "cross_module"},
    )
