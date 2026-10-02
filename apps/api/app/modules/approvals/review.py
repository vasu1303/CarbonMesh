"""Read-only domain revalidation immediately before a generic approval commits."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.assurance import DisclosureDraft
from app.db.models.core import Approval
from app.db.models.dispatch import DispatchRecommendation
from app.db.models.ledger import FactBinding
from app.db.models.procurement import Recommendation
from app.modules.approvals import repository
from app.modules.ledger.service import normalize_json, payload_sha256


async def generic_preview_is_current(
    session: AsyncSession,
    approval: Approval,
    *,
    target: DisclosureDraft | DispatchRecommendation | None = None,
) -> bool:
    from app.modules.assurance.errors import AssuranceError
    from app.modules.dispatch.errors import DispatchError

    if approval.status in {"invalidated", "expired"} or approval.target_id is None:
        return False
    payload = approval.preview_payload
    if not payload or (
        payload.get("target_type") != approval.target_type
        or payload.get("target_id") != str(approval.target_id)
        or payload.get("analysis_signature") != approval.analysis_signature
        or payload.get("context_hash") != approval.context_hash
        or payload_sha256(payload)[1] != approval.preview_hash
    ):
        return False
    try:
        if datetime.fromisoformat(str(payload.get("approval_expires_at"))) != approval.expires_at:
            return False
        target = target or await repository.get_generic_target(session, approval=approval)
        if (
            target is None
            or target.invalidated_at is not None
            or target.status not in {"pending_approval", "approved", "rejected"}
            or target.payload_hash != approval.preview_hash
            or target.ledger_event_id is None
        ):
            return False
        event = await repository.get_ledger_event(
            session, company_id=approval.company_id, event_id=target.ledger_event_id
        )
        if (
            event is None
            or event.entity_type != approval.target_type
            or event.entity_id != approval.target_id
            or event.analysis_signature != approval.analysis_signature
            or event.created_by != approval.requested_by
            or event.payload_hash != approval.preview_hash
            or event.payload != payload
        ):
            return False
        if isinstance(target, DisclosureDraft):
            return await _disclosure_current(session, approval, target)
        if isinstance(target, DispatchRecommendation):
            return await _dispatch_current(session, approval, target)
    except (AssuranceError, DispatchError, LookupError, TypeError, ValueError, ArithmeticError):
        # Invalid/missing persisted domain data cannot authorize a decision. Database
        # failures deliberately propagate so infrastructure errors remain distinguishable.
        return False
    return False


def procurement_bindings_are_current(
    recommendation: Recommendation, bindings: list[FactBinding]
) -> bool:
    expected = recommendation.impact_snapshot.get("review", {}).get("fact_bindings", [])
    if not bindings or len(bindings) != len(expected):
        return False
    by_placeholder = {item.placeholder: item for item in bindings}
    for snapshot in expected:
        binding = by_placeholder.get(snapshot["placeholder"])
        if (
            binding is None
            or binding.ledger_event_id != recommendation.ledger_event_id
            or binding.artifact_id != recommendation.id
            or binding.artifact_type != "procurement_recommendation"
            or binding.context_hash != recommendation.analysis_signature
            or binding.value_snapshot != snapshot["value"]
            or binding.display_value != snapshot["display_value"]
            or binding.unit != snapshot["unit"]
            or (str(binding.evidence_item_id) if binding.evidence_item_id else None)
            != snapshot["evidence_item_id"]
        ):
            return False
        material = {
            "artifact_type": binding.artifact_type,
            "artifact_id": binding.artifact_id,
            "placeholder": binding.placeholder,
            "ledger_event_id": binding.ledger_event_id,
            "evidence_item_id": binding.evidence_item_id,
            "value_snapshot": binding.value_snapshot,
            "display_value": binding.display_value,
            "unit": binding.unit,
            "context_hash": binding.context_hash,
        }
        if payload_sha256(material)[1] != binding.binding_hash:
            return False
    return True


async def _disclosure_current(
    session: AsyncSession, approval: Approval, draft: DisclosureDraft
) -> bool:
    # Lazy import: Assurance creates previews through the shared approval service.
    from app.modules.assurance.service import AssuranceService

    service = AssuranceService(session)
    aggregate = await service.repository.load_draft_aggregate(
        company_id=approval.company_id, draft_id=draft.id
    )
    if (
        aggregate is None
        or draft.context_hash != approval.context_hash
        or draft.validation_summary.get("analysis_signature") != approval.analysis_signature
        or not aggregate.claims
        or any(
            claim.support_status != "supported"
            and service._requirement_is_required(claim.requirement_id, aggregate.requirements)
            for claim in aggregate.claims
        )
        or any(gap.status == "open" and gap.severity == "error" for gap in aggregate.gaps)
        or await service._aggregate_is_stale(aggregate)
    ):
        return False
    rebuilt = service._preview_payload(
        aggregate,
        context_hash=draft.context_hash,
        analysis_signature=approval.analysis_signature,
        approval_expires_at=approval.expires_at,
        approval_eligible=True,
    )
    return payload_sha256(rebuilt)[1] == approval.preview_hash


async def _dispatch_current(
    session: AsyncSession, approval: Approval, recommendation: DispatchRecommendation
) -> bool:
    from app.modules.dispatch.schemas import FrozenDispatchConstraints
    from app.modules.dispatch.service import DispatchService, _constraint_view

    service = DispatchService(session)
    record = await service.repository.get_scenario(
        company_id=approval.company_id, scenario_id=recommendation.dispatch_scenario_id
    )
    if record is None:
        return False
    scenario = record.scenario
    payload = approval.preview_payload
    if (
        scenario.status in {"invalidated", "closed", "no_feasible_window"}
        or recommendation.actuation_authorized
        or recommendation.analysis_signature != approval.analysis_signature
        or scenario.analysis_signature != approval.analysis_signature
        or scenario.context_hash != approval.context_hash
        or scenario.policy_definition_id != approval.policy_definition_id
        or payload.get("scenario_id") != str(scenario.id)
        or payload.get("advisory_only") is not True
        or payload.get("actuation_authorized") is not False
    ):
        return False
    frozen = FrozenDispatchConstraints.model_validate(scenario.constraint_snapshot)
    reviewed = FrozenDispatchConstraints.model_validate(payload["frozen_constraints"])
    if (
        frozen != reviewed
        or frozen.requested_by != approval.requested_by
        or frozen.approval_idempotency_key != approval.idempotency_key
    ):
        return False
    load, method, policy = service._validate_dependency_snapshots(record, frozen)
    if (
        payload["load_snapshot"] != normalize_json(load)
        or payload["method_snapshot"] != normalize_json(method)
        or payload["policy_snapshot"] != normalize_json(policy)
        or payload["constraint_snapshot"]
        != normalize_json(service._optimization_constraint_payload(frozen))
    ):
        return False
    current_constraints = [
        _constraint_view(item)
        for item in record.constraints
        if item.is_active
        and (item.valid_from is None or item.valid_from < scenario.window_end)
        and (item.valid_to is None or item.valid_to > scenario.window_start)
    ]
    if current_constraints != frozen.source_constraints:
        return False
    context = {
        "company_id": scenario.company_id,
        "site_id": scenario.site_id,
        "flexible_load_id": scenario.flexible_load_id,
        "requested_by": frozen.requested_by,
        "window_start": scenario.window_start,
        "window_end": scenario.window_end,
        "objective": scenario.objective,
    }
    if scenario.agent_run_id is not None:
        context["agent_run_id"] = scenario.agent_run_id
    if payload_sha256(context)[1] != approval.context_hash:
        return False
    for field in ("recommended_start", "recommended_end", "baseline_start", "baseline_end"):
        if datetime.fromisoformat(payload[field]) != getattr(recommendation, field):
            return False
    for field in (
        "expected_emissions_kgco2e",
        "baseline_emissions_kgco2e",
        "avoided_kgco2e",
        "reduction_pct",
    ):
        if Decimal(payload[field]) != getattr(recommendation, field):
            return False
    source_document = await service.repository.get_source_document(
        company_id=approval.company_id,
        source_document_id=scenario.forecast_source_document_id,
    )
    if (
        source_document is None
        or payload["forecast_source_document_id"] != str(source_document.id)
        or payload["forecast_source_document_checksum"] != source_document.checksum
    ):
        return False
    current_forecasts = await service.repository.list_forecast_points(
        company_id=approval.company_id, source_document_id=source_document.id
    )
    await service._validate_forecast_provenance(
        current_forecasts,
        company_id=approval.company_id,
        source_document=source_document,
        site_id=scenario.site_id,
        window_start=scenario.window_start,
        window_end=scenario.window_end,
        expected_snapshot=scenario.forecast_snapshot,
    )
    if payload["forecast_points"] != [
        {field: item[field] for field in payload["forecast_points"][0]}
        for item in scenario.forecast_snapshot
    ]:
        return False
    bindings = (
        await repository.list_fact_bindings(
            session,
            company_id=approval.company_id,
            artifact_type="dispatch_recommendation",
            artifact_ids=[recommendation.id],
        )
    ).get(recommendation.id, [])
    expected_bindings = {
        "fact_expected_emissions": (recommendation.expected_emissions_kgco2e, "kgCO2e"),
        "fact_baseline_emissions": (recommendation.baseline_emissions_kgco2e, "kgCO2e"),
        "fact_avoided_emissions": (recommendation.avoided_kgco2e, "kgCO2e"),
        "fact_reduction_pct": (recommendation.reduction_pct, "%"),
    }
    if {item.placeholder for item in bindings} != set(expected_bindings):
        return False
    for binding in bindings:
        value, unit = expected_bindings[binding.placeholder]
        if (
            binding.value_snapshot != {"value": str(value), "unit": unit}
            or binding.unit != unit
            or binding.display_value != f"{value} {unit}"
            or binding.ledger_event_id != recommendation.ledger_event_id
            or binding.context_hash != scenario.context_hash
        ):
            return False
        material = {
            "artifact_type": binding.artifact_type,
            "artifact_id": binding.artifact_id,
            "placeholder": binding.placeholder,
            "value": binding.value_snapshot,
            "ledger_event_id": binding.ledger_event_id,
            "context_hash": binding.context_hash,
        }
        if payload_sha256(material)[1] != binding.binding_hash:
            return False
    return True
