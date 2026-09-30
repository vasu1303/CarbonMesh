from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.core import AuditLog
from app.db.models.procurement import Approval, Recommendation
from app.modules.approvals import repository
from app.modules.approvals.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResult,
    ApprovalItem,
    ApprovalListResult,
)
from app.modules.ledger.service import append_ledger_event, append_lineage_edge
from app.modules.procurement.review import (
    recommendation_preview_is_current,
    recommendation_previews_are_current,
)

DEFAULT_APPROVAL_TTL = timedelta(hours=24)


class ApprovalServiceError(RuntimeError):
    code = "approval_error"
    status_code = 400
    retryable = False

    def __init__(self, message: str, *, field_details: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field_details = field_details or {}


class ApprovalNotFoundError(ApprovalServiceError):
    code = "approval_not_found"
    status_code = 404


class ApprovalConflictError(ApprovalServiceError):
    code = "approval_conflict"
    status_code = 409


class ApprovalInvalidatedError(ApprovalServiceError):
    code = "approval_invalidated"
    status_code = 409


class ApprovalExpiredError(ApprovalServiceError):
    code = "approval_expired"
    status_code = 409


class ApprovalActorError(ApprovalServiceError):
    code = "invalid_approval_actor"
    status_code = 403


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def create_pending_approval(
    session: AsyncSession,
    *,
    recommendation: Recommendation,
    requested_by: UUID,
    expires_at: datetime | None = None,
    idempotency_key: str | None = None,
) -> Approval:
    """Create an approval preview in the caller-owned procurement transaction."""
    existing = await repository.get_pending_approval(
        session,
        company_id=recommendation.company_id,
        recommendation_id=recommendation.id,
    )
    if existing is not None:
        if (
            existing.preview_hash == recommendation.payload_hash
            and existing.analysis_signature == recommendation.analysis_signature
        ):
            return existing
        raise ApprovalConflictError("A different pending preview already exists.")

    if expires_at is None:
        expires_at = datetime.now(UTC) + DEFAULT_APPROVAL_TTL
    else:
        expires_at = _as_utc(expires_at)
    if expires_at <= datetime.now(UTC):
        raise ApprovalExpiredError("Approval expiration must be in the future.")

    if idempotency_key is None:
        material = (
            f"approval:{recommendation.company_id}:{recommendation.id}:"
            f"{recommendation.payload_hash}"
        )
        idempotency_key = hashlib.sha256(material.encode("utf-8")).hexdigest()

    approval = Approval(
        company_id=recommendation.company_id,
        recommendation_id=recommendation.id,
        requested_by=requested_by,
        status="pending",
        preview_hash=recommendation.payload_hash,
        analysis_signature=recommendation.analysis_signature,
        idempotency_key=idempotency_key,
        expires_at=expires_at,
    )
    session.add(approval)
    await session.flush()
    return approval


async def list_approvals(
    session: AsyncSession,
    *,
    company_id: UUID,
    status: Literal["pending", "approved", "rejected"] | None,
    limit: int,
    offset: int,
) -> ApprovalListResult:
    records, total = await repository.list_approval_records(
        session,
        company_id=company_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    recommendations = [recommendation for _, recommendation, _, _ in records]
    review_inputs = await repository.load_recommendation_review_inputs(
        session,
        company_id=company_id,
        recommendations=recommendations,
    )
    preview_statuses = recommendation_previews_are_current(recommendations, review_inputs)
    now = datetime.now(UTC)
    items = []
    for approval, recommendation, requester, decider in records:
        review = recommendation.impact_snapshot.get("review", {})
        recommended_snapshot = review.get("recommended_product", {})
        impact_snapshot = review.get("supplier_score", {}).get("impact", {})
        items.append(
            ApprovalItem(
                id=approval.id,
                company_id=approval.company_id,
                recommendation_id=approval.recommendation_id,
                status=approval.status,
                preview_hash=approval.preview_hash,
                analysis_signature=approval.analysis_signature,
                expires_at=approval.expires_at,
                created_at=approval.created_at,
                requested_by=approval.requested_by,
                requester_name=requester.display_name,
                decided_by=approval.decided_by,
                decider_name=decider.display_name if decider is not None else None,
                decided_at=approval.decided_at,
                decision_note=approval.decision_note,
                ledger_event_id=approval.ledger_event_id,
                recommended_product_id=recommendation.recommended_product_id,
                recommended_product_name=recommended_snapshot.get(
                    "name", "Unavailable frozen product"
                ),
                supplier_name=recommended_snapshot.get(
                    "supplier_name", "Unavailable frozen supplier"
                ),
                projected_footprint_kgco2e=impact_snapshot.get(
                    "projected_footprint_kgco2e",
                    recommendation.projected_footprint_kgco2e,
                ),
                avoided_kgco2e=impact_snapshot.get("avoided_kgco2e", recommendation.avoided_kgco2e),
                reduction_pct=impact_snapshot.get("reduction_pct", recommendation.reduction_pct),
                cost_delta_pct=impact_snapshot.get("cost_delta_pct", recommendation.cost_delta_pct),
                lead_time_delta_days=impact_snapshot.get(
                    "lead_time_delta_days", recommendation.lead_time_delta_days
                ),
                expired=_as_utc(approval.expires_at) <= now,
                preview_current=(
                    approval.preview_hash == recommendation.payload_hash
                    and approval.analysis_signature == recommendation.analysis_signature
                    and recommendation.invalidated_at is None
                    and recommendation.status != "invalidated"
                    and preview_statuses.get(recommendation.id, False)
                ),
            )
        )
    return ApprovalListResult(items=items, total=total, limit=limit, offset=offset)


async def decide_approval(
    session: AsyncSession,
    *,
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    trace_id: str | None = None,
) -> ApprovalDecisionResult:
    approval = await repository.get_approval_for_update(
        session,
        company_id=request.company_id,
        approval_id=approval_id,
    )
    if approval is None:
        raise ApprovalNotFoundError("The requested approval was not found.")

    actor = await repository.get_actor(
        session,
        company_id=approval.company_id,
        actor_id=request.actor_id,
    )
    if actor is None or not actor.is_active or actor.role != "approver":
        raise ApprovalActorError("An active approver must make this decision.")

    expected_status = "approved" if request.decision == "approve" else "rejected"
    if approval.status != "pending":
        if (
            approval.status == expected_status
            and approval.preview_hash == request.preview_hash
            and approval.decided_by == request.actor_id
            and approval.decided_at is not None
            and approval.ledger_event_id is not None
        ):
            return ApprovalDecisionResult(
                approval_id=approval.id,
                recommendation_id=approval.recommendation_id,
                status=approval.status,
                preview_hash=approval.preview_hash,
                analysis_signature=approval.analysis_signature,
                decided_by=approval.decided_by,
                decided_at=approval.decided_at,
                decision_note=approval.decision_note,
                ledger_event_id=approval.ledger_event_id,
                idempotent_replay=True,
            )
        raise ApprovalConflictError("This approval already has a different decision.")

    if _as_utc(approval.expires_at) <= datetime.now(UTC):
        raise ApprovalExpiredError("The approval preview has expired.")
    if request.preview_hash != approval.preview_hash:
        raise ApprovalInvalidatedError(
            "The submitted preview hash does not match the reviewed payload.",
            field_details={"preview_hash": "mismatch"},
        )

    recommendation = await repository.get_recommendation_for_update(
        session,
        company_id=approval.company_id,
        recommendation_id=approval.recommendation_id,
    )
    if recommendation is None:
        raise ApprovalInvalidatedError("The bound recommendation no longer exists.")
    if (
        recommendation.payload_hash != approval.preview_hash
        or recommendation.analysis_signature != approval.analysis_signature
        or recommendation.status != "pending_approval"
        or recommendation.invalidated_at is not None
    ):
        raise ApprovalInvalidatedError("The recommendation changed after preview creation.")
    if not await recommendation_preview_is_current(session, recommendation):
        raise ApprovalInvalidatedError(
            "A reviewed product, evidence item, scenario, or scoring method changed."
        )

    decided_at = datetime.now(UTC)
    ledger_event = await append_ledger_event(
        session,
        company_id=approval.company_id,
        event_type=f"approval.{expected_status}",
        entity_type="approval",
        entity_id=approval.id,
        payload={
            "approval_id": approval.id,
            "recommendation_id": approval.recommendation_id,
            "decision": expected_status,
            "preview_hash": approval.preview_hash,
            "analysis_signature": approval.analysis_signature,
            "decided_by": request.actor_id,
            "decided_at": decided_at,
            "decision_note": request.decision_note,
        },
        analysis_signature=approval.analysis_signature,
        created_by=request.actor_id,
    )

    approval.status = expected_status
    approval.decided_by = request.actor_id
    approval.decided_at = decided_at
    approval.decision_note = request.decision_note
    approval.ledger_event_id = ledger_event.id
    recommendation.status = expected_status

    if recommendation.ledger_event_id is not None:
        await append_lineage_edge(
            session,
            company_id=approval.company_id,
            parent_event_id=recommendation.ledger_event_id,
            child_event_id=ledger_event.id,
            relationship_type="decided_by",
            metadata={"decision": expected_status},
        )

    session.add(
        AuditLog(
            company_id=approval.company_id,
            actor_id=request.actor_id,
            action=f"approval.{expected_status}",
            entity_type="approval",
            entity_id=approval.id,
            trace_id=trace_id,
            details={
                "recommendation_id": str(approval.recommendation_id),
                "preview_hash": approval.preview_hash,
                "analysis_signature": approval.analysis_signature,
                "ledger_event_id": str(ledger_event.id),
            },
        )
    )
    await session.commit()

    return ApprovalDecisionResult(
        approval_id=approval.id,
        recommendation_id=approval.recommendation_id,
        status=expected_status,
        preview_hash=approval.preview_hash,
        analysis_signature=approval.analysis_signature,
        decided_by=request.actor_id,
        decided_at=decided_at,
        decision_note=request.decision_note,
        ledger_event_id=ledger_event.id,
    )
