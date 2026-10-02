"""Conversion between the API context contract and immutable graph context."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.modules.agents.graph_contracts import (
    ContextConstraint,
    ContextEntity,
    ContextEnvelope,
    ExecutionPlan,
    ModuleName,
)
from app.modules.agents.schemas import (
    AgentContextRequest,
    FrozenContextEnvelope,
    ResolvedWorkflowContext,
    Workflow,
)

_DEFAULT_MODULE_METRICS: dict[ModuleName, tuple[str, ...]] = {
    "measurement": ("emissions.scope2.location_based", "emissions.scope3.category1"),
    "assurance": ("emissions.scope2.location_based",),
    "procurement": (
        "emissions.scope3.category1",
        "procurement.projected_avoided_emissions",
        "procurement.cost_delta_pct",
    ),
    "dispatch": ("emissions.scope2.location_based",),
}

_DEFAULT_WORKFLOW_METRICS: dict[Workflow, tuple[str, ...]] = {
    "planning": (),
    "measurement": _DEFAULT_MODULE_METRICS["measurement"],
    "assurance": _DEFAULT_MODULE_METRICS["assurance"],
    "procurement": _DEFAULT_MODULE_METRICS["procurement"],
    "dispatch": _DEFAULT_MODULE_METRICS["dispatch"],
    "cross_module": tuple(
        dict.fromkeys(
            (*_DEFAULT_MODULE_METRICS["measurement"], *_DEFAULT_MODULE_METRICS["procurement"])
        )
    ),
    "four_module": tuple(
        dict.fromkeys(
            metric
            for module in ("measurement", "assurance", "procurement", "dispatch")
            for metric in _DEFAULT_MODULE_METRICS[module]
        )
    ),
    "unsupported": (),
}

_SITE_REFERENCE_PATTERN = re.compile(
    r"(?<![a-z0-9])(?P<site>plant\s+[a-z0-9][a-z0-9_-]*)(?![a-z0-9_-])",
    re.IGNORECASE,
)
_QUARTER_REFERENCE_PATTERNS = (
    re.compile(
        r"(?<![a-z0-9])q(?P<quarter>[1-4])\s*[,/-]?\s*(?P<year>20\d{2})(?!\d)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<!\d)(?P<year>20\d{2})\s*[,/-]?\s*q(?P<quarter>[1-4])(?![a-z0-9])",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True, slots=True)
class QueryContextHints:
    """Exact entity hints extracted without asking a model to invent IDs."""

    site_reference: str | None = None
    period_start: date | None = None
    period_end: date | None = None

def extract_query_context_hints(query: str) -> QueryContextHints:
    """Extract only the narrow site/quarter syntax supported by the demo.

    Matching a name to an identifier remains a tenant-scoped repository read.
    This parser intentionally performs no fuzzy matching and never persists the
    query text.
    """

    site_match = _SITE_REFERENCE_PATTERN.search(query)
    quarter_match = None
    for pattern in _QUARTER_REFERENCE_PATTERNS:
        quarter_match = pattern.search(query)
        if quarter_match is not None:
            break
    period_start: date | None = None
    period_end: date | None = None
    if quarter_match is not None:
        year = int(quarter_match.group("year"))
        quarter = int(quarter_match.group("quarter"))
        start_month = ((quarter - 1) * 3) + 1
        period_start = date(year, start_month, 1)
        next_quarter = (
            date(year + 1, 1, 1)
            if quarter == 4
            else date(year, start_month + 3, 1)
        )
        period_end = next_quarter - timedelta(days=1)
    return QueryContextHints(
        site_reference=(
            "-".join(site_match.group("site").casefold().split())
            if site_match is not None
            else None
        ),
        period_start=period_start,
        period_end=period_end,
    )


def workflow_for_modules(modules: tuple[ModuleName, ...]) -> Workflow:
    if len(modules) == 1:
        return modules[0]
    if modules == ("measurement", "procurement"):
        return "cross_module"
    return "four_module"


def build_frozen_context(
    *,
    request_context: AgentContextRequest,
    actor_role: str,
    query: str,
    workflow: Workflow,
    resolved: ResolvedWorkflowContext | None = None,
) -> FrozenContextEnvelope:
    """Build the persisted API context without retaining the user prompt."""

    metric_keys = request_context.metric_keys or list(_DEFAULT_WORKFLOW_METRICS[workflow])
    unsigned = {
        "company_id": str(request_context.company_id),
        "grid_source_mode": request_context.grid_source_mode,
        "fresh_inputs": request_context.fresh_inputs.model_dump(mode="json") if request_context.fresh_inputs else None,
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
        "activity_record_ids": [str(value) for value in request_context.activity_record_ids],
        "standard_id": str(request_context.standard_id) if request_context.standard_id else None,
        "disclosure_draft_id": (
            str(request_context.disclosure_draft_id)
            if request_context.disclosure_draft_id
            else None
        ),
        "requirement_ids": [str(value) for value in request_context.requirement_ids],
        "evidence_item_ids": [str(value) for value in request_context.evidence_item_ids],
        "procurement_scenario_id": (
            str(request_context.procurement_scenario_id)
            if request_context.procurement_scenario_id
            else None
        ),
        "flexible_load_id": (
            str(request_context.flexible_load_id)
            if request_context.flexible_load_id
            else None
        ),
        "dispatch_scenario_id": (
            str(request_context.dispatch_scenario_id)
            if request_context.dispatch_scenario_id
            else None
        ),
        "forecast_id": str(request_context.forecast_id) if request_context.forecast_id else None,
        "policy_definition_id": (
            str(request_context.policy_definition_id)
            if request_context.policy_definition_id
            else None
        ),
        "workflow": workflow,
        "metric_keys": metric_keys,
        "material_scope": request_context.material_scope,
        "supplier_product_ids": [str(value) for value in request_context.supplier_product_ids],
        "actor_id": str(request_context.actor_id),
        "actor_role": actor_role,
        "constraints": request_context.constraints.model_dump(mode="json"),
        "dispatch_constraints": request_context.dispatch_constraints.model_dump(mode="json"),
        "resolved": resolved.model_dump(mode="json") if resolved else None,
        "request_hash": hashlib.sha256(query.encode("utf-8")).hexdigest(),
    }
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return FrozenContextEnvelope.model_validate(
        {**unsigned, "analysis_signature": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
    )


def freeze_runtime_context(
    *,
    request_context: AgentContextRequest,
    actor_role: str,
    query: str,
    plan: ExecutionPlan,
) -> tuple[FrozenContextEnvelope, ContextEnvelope]:
    workflow = workflow_for_modules(plan.modules)
    api_context = build_frozen_context(
        request_context=request_context,
        actor_role=actor_role,
        query=query,
        workflow=workflow,
    )
    return _freeze_with_request_hash(
        request_context=request_context,
        actor_role=actor_role,
        request_hash=api_context.request_hash,
        plan=plan,
        api_context=api_context,
    )


def freeze_runtime_context_from_hash(
    *,
    request_context: AgentContextRequest,
    actor_role: str,
    request_hash: str,
    plan: ExecutionPlan,
) -> tuple[FrozenContextEnvelope, ContextEnvelope]:
    """Re-freeze clarified context without persisting or reconstructing the prompt."""

    workflow = workflow_for_modules(plan.modules)
    unsigned_api = {
        "company_id": request_context.company_id,
        "grid_source_mode": request_context.grid_source_mode,
        "fresh_inputs": request_context.fresh_inputs,
        "site_id": request_context.site_id,
        "reporting_period_id": request_context.reporting_period_id,
        "carbon_measurement_id": request_context.carbon_measurement_id,
        "current_product_id": request_context.current_product_id,
        "method_definition_id": request_context.method_definition_id,
        "activity_record_ids": request_context.activity_record_ids,
        "standard_id": request_context.standard_id,
        "disclosure_draft_id": request_context.disclosure_draft_id,
        "requirement_ids": request_context.requirement_ids,
        "evidence_item_ids": request_context.evidence_item_ids,
        "procurement_scenario_id": request_context.procurement_scenario_id,
        "flexible_load_id": request_context.flexible_load_id,
        "dispatch_scenario_id": request_context.dispatch_scenario_id,
        "forecast_id": request_context.forecast_id,
        "policy_definition_id": request_context.policy_definition_id,
        "workflow": workflow,
        "metric_keys": request_context.metric_keys,
        "material_scope": request_context.material_scope,
        "supplier_product_ids": request_context.supplier_product_ids,
        "actor_id": request_context.actor_id,
        "actor_role": actor_role,
        "constraints": request_context.constraints,
        "dispatch_constraints": request_context.dispatch_constraints,
        "resolved": None,
        "request_hash": request_hash,
    }
    api_context = FrozenContextEnvelope.model_validate(
        {**unsigned_api, "analysis_signature": "0" * 64}
    )
    return _freeze_with_request_hash(
        request_context=request_context,
        actor_role=actor_role,
        request_hash=request_hash,
        plan=plan,
        api_context=api_context,
    )


def _freeze_with_request_hash(
    *,
    request_context: AgentContextRequest,
    actor_role: str,
    request_hash: str,
    plan: ExecutionPlan,
    api_context: FrozenContextEnvelope,
) -> tuple[FrozenContextEnvelope, ContextEnvelope]:
    metrics = tuple(request_context.metric_keys) or tuple(
        dict.fromkeys(
            metric
            for module in plan.modules
            for metric in _DEFAULT_MODULE_METRICS[module]
        )
    )
    constraints = tuple(
        ContextConstraint(key=key, value=_canonical_value(value))
        for key, value in request_context.constraints.model_dump(mode="json").items()
        if value is not None
    )
    constraints += tuple(
        ContextConstraint(key=f"dispatch.{key}", value=_canonical_value(value))
        for key, value in request_context.dispatch_constraints.model_dump(mode="json").items()
        if value not in (None, [])
    )
    constraints += (
        ContextConstraint(key="integration.grid_source_mode", value=request_context.grid_source_mode),
    )
    entities: list[ContextEntity] = []
    if request_context.carbon_measurement_id is not None:
        entities.append(
            ContextEntity(
                entity_type="carbon_measurement",
                entity_id=request_context.carbon_measurement_id,
            )
        )
    if request_context.current_product_id is not None:
        entities.append(
            ContextEntity(
                entity_type="current_product",
                entity_id=request_context.current_product_id,
            )
        )
    entities.extend(
        ContextEntity(entity_type="supplier_product", entity_id=product_id)
        for product_id in request_context.supplier_product_ids
        if product_id != request_context.current_product_id
    )
    entities.extend(
        ContextEntity(entity_type="activity_record", entity_id=record_id)
        for record_id in request_context.activity_record_ids
    )
    optional_entities = (
        ("standard", request_context.standard_id),
        ("disclosure_draft", request_context.disclosure_draft_id),
        ("procurement_scenario", request_context.procurement_scenario_id),
        ("flexible_load", request_context.flexible_load_id),
        ("dispatch_scenario", request_context.dispatch_scenario_id),
        ("grid_forecast", request_context.forecast_id),
        ("policy_definition", request_context.policy_definition_id),
    )
    entities.extend(
        ContextEntity(entity_type=entity_type, entity_id=entity_id)
        for entity_type, entity_id in optional_entities
        if entity_id is not None
    )
    entities.extend(
        ContextEntity(entity_type="disclosure_requirement", entity_id=requirement_id)
        for requirement_id in request_context.requirement_ids
    )
    entities.extend(
        ContextEntity(entity_type="evidence_item", entity_id=evidence_id)
        for evidence_id in request_context.evidence_item_ids
    )
    method_ids = (
        (request_context.method_definition_id,)
        if request_context.method_definition_id is not None
        else ()
    )
    unsigned: dict[str, Any] = {
        "company_id": request_context.company_id,
        "fresh_inputs": request_context.fresh_inputs,
        "site_id": request_context.site_id,
        "reporting_period_id": request_context.reporting_period_id,
        "actor_id": request_context.actor_id,
        "actor_role": actor_role,
        "modules": plan.modules,
        "metric_keys": metrics,
        "material_scope": tuple(request_context.material_scope),
        "entities": tuple(entities),
        "method_definition_ids": method_ids,
        "constraints": constraints,
        "request_hash": request_hash,
    }
    signature = _hash(unsigned)
    graph_context = ContextEnvelope(**unsigned, analysis_signature=signature)
    api_context = api_context.model_copy(
        update={
            "metric_keys": list(metrics),
            "analysis_signature": signature,
        }
    )
    return api_context, graph_context


def _canonical_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _jsonable(value: object) -> object:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))  # type: ignore[attr-defined]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return str(value) if not isinstance(value, (str, int, bool, type(None))) else value
