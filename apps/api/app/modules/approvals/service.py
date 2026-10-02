from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.core import Actor, Approval, AuditLog
from app.db.models.procurement import Recommendation
from app.modules.approvals import repository
from app.modules.approvals.review import (
    generic_preview_is_current,
    procurement_bindings_are_current,
)
from app.modules.approvals.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResult,
    ApprovalDetail,
    ApprovalItem,
    ApprovalListResult,
    ApprovalStatus,
    GenericApprovalPreviewRequest,
    GenericApprovalPreviewResult,
)
from app.modules.ledger.service import (
    append_ledger_event,
    append_lineage_edge,
    payload_sha256,
)
from app.modules.procurement.review import (
    recommendation_preview_is_current,
    recommendation_previews_are_current,
    stored_recommendation_payload,
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


class ApprovalPreviewValidationError(ApprovalServiceError):
    code = "approval_preview_invalid"
    status_code = 422


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _generic_idempotency_key(
    request: GenericApprovalPreviewRequest,
) -> str:
    if request.idempotency_key is not None:
        return request.idempotency_key
    material = (
        f"approval:{request.company_id}:{request.target_type}:"
        f"{request.target_id}:{request.preview_hash}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _validate_preview_bindings(
    request: GenericApprovalPreviewRequest,
    normalized_payload: dict[str, object],
    calculated_hash: str,
) -> None:
    if calculated_hash != request.preview_hash:
        raise ApprovalPreviewValidationError(
            "The preview hash does not match the canonical preview payload.",
            field_details={"preview_hash": "mismatch"},
        )

    expected = {
        "target_type": request.target_type,
        "target_id": str(request.target_id),
        "analysis_signature": request.analysis_signature,
        "context_hash": request.context_hash,
    }
    mismatched = {
        field: "missing_or_mismatch"
        for field, value in expected.items()
        if normalized_payload.get(field) != value
    }
    if mismatched:
        raise ApprovalPreviewValidationError(
            "The preview payload is not bound to its target and analysis context.",
            field_details=mismatched,
        )


def _generic_preview_matches(
    approval: Approval,
    *,
    request: GenericApprovalPreviewRequest,
    normalized_payload: dict[str, object],
    idempotency_key: str,
) -> bool:
    return (
        approval.target_type == request.target_type
        and approval.target_id == request.target_id
        and approval.recommendation_id is None
        and approval.requested_by == request.requested_by
        and approval.preview_payload == normalized_payload
        and approval.preview_hash == request.preview_hash
        and approval.analysis_signature == request.analysis_signature
        and approval.context_hash == request.context_hash
        and approval.idempotency_key == idempotency_key
        and approval.policy_definition_id == request.policy_definition_id
        and (
            request.expires_at is None
            or _as_utc(approval.expires_at) == _as_utc(request.expires_at)
        )
    )


def _generic_preview_result(
    approval: Approval,
    *,
    idempotent_replay: bool,
) -> GenericApprovalPreviewResult:
    if approval.target_id is None or approval.context_hash is None:
        raise ApprovalConflictError("The stored generic approval preview is incomplete.")
    return GenericApprovalPreviewResult(
        approval_id=approval.id,
        company_id=approval.company_id,
        target_type=approval.target_type,
        target_id=approval.target_id,
        requested_by=approval.requested_by,
        status=approval.status,
        preview_payload=approval.preview_payload,
        preview_hash=approval.preview_hash,
        analysis_signature=approval.analysis_signature,
        context_hash=approval.context_hash,
        idempotency_key=approval.idempotency_key,
        policy_definition_id=approval.policy_definition_id,
        expires_at=approval.expires_at,
        idempotent_replay=idempotent_replay,
    )


async def create_generic_approval_preview(
    session: AsyncSession,
    request: GenericApprovalPreviewRequest,
) -> GenericApprovalPreviewResult:
    """Create or replay an exact generic preview without owning the transaction."""
    actor = await repository.get_actor(
        session,
        company_id=request.company_id,
        actor_id=request.requested_by,
    )
    if actor is None or not actor.is_active:
        raise ApprovalActorError(
            "An active actor in the target company must request the approval preview.",
            field_details={"requested_by": "inactive_or_not_found"},
        )

    try:
        normalized_payload, calculated_hash = payload_sha256(request.preview_payload)
    except TypeError as error:
        raise ApprovalPreviewValidationError(
            "The preview payload contains a value that cannot be canonicalized.",
            field_details={"preview_payload": "unsupported_value"},
        ) from error
    _validate_preview_bindings(request, normalized_payload, calculated_hash)

    idempotency_key = _generic_idempotency_key(request)
    existing_by_key = await repository.get_approval_by_idempotency_key(
        session,
        company_id=request.company_id,
        idempotency_key=idempotency_key,
    )
    if existing_by_key is not None:
        if _generic_preview_matches(
            existing_by_key,
            request=request,
            normalized_payload=normalized_payload,
            idempotency_key=idempotency_key,
        ):
            return _generic_preview_result(existing_by_key, idempotent_replay=True)
        raise ApprovalConflictError(
            "The idempotency key is already bound to a different approval preview.",
            field_details={"idempotency_key": "conflict"},
        )

    existing_target = await repository.get_pending_approval_for_target(
        session,
        company_id=request.company_id,
        target_type=request.target_type,
        target_id=request.target_id,
    )
    if existing_target is not None:
        if _generic_preview_matches(
            existing_target,
            request=request,
            normalized_payload=normalized_payload,
            idempotency_key=idempotency_key,
        ):
            return _generic_preview_result(existing_target, idempotent_replay=True)
        raise ApprovalConflictError(
            "A different pending preview already exists for this target.",
            field_details={"target_id": "pending_preview_conflict"},
        )

    now = datetime.now(UTC)
    expires_at = (
        now + DEFAULT_APPROVAL_TTL if request.expires_at is None else _as_utc(request.expires_at)
    )
    if expires_at <= now:
        raise ApprovalExpiredError("Approval expiration must be in the future.")

    approval = Approval(
        company_id=request.company_id,
        target_type=request.target_type,
        target_id=request.target_id,
        recommendation_id=None,
        requested_by=request.requested_by,
        policy_definition_id=request.policy_definition_id,
        status="pending",
        preview_payload=normalized_payload,
        preview_hash=request.preview_hash,
        analysis_signature=request.analysis_signature,
        context_hash=request.context_hash,
        idempotency_key=idempotency_key,
        expires_at=expires_at,
    )
    session.add(approval)
    await session.flush()
    return _generic_preview_result(approval, idempotent_replay=False)


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
        target_type="procurement_recommendation",
        target_id=recommendation.id,
        recommendation_id=recommendation.id,
        requested_by=requested_by,
        status="pending",
        preview_hash=recommendation.payload_hash,
        preview_payload=payload_sha256(stored_recommendation_payload(recommendation))[0],
        analysis_signature=recommendation.analysis_signature,
        context_hash=recommendation.analysis_signature,
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
    status: ApprovalStatus | None,
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
    recommendations = [item for _, item, _, _ in records if item is not None]
    review_inputs = await repository.load_recommendation_review_inputs(
        session,
        company_id=company_id,
        recommendations=recommendations,
    )
    preview_statuses = recommendation_previews_are_current(recommendations, review_inputs)
    bindings = await repository.list_fact_bindings(
        session,
        company_id=company_id,
        artifact_type="procurement_recommendation",
        artifact_ids=[item.id for item in recommendations],
    )
    items = []
    for approval, recommendation, requester, decider in records:
        if recommendation is None:
            current = await generic_preview_is_current(session, approval)
        else:
            current = _procurement_preview_matches(approval, recommendation) and (
                preview_statuses.get(recommendation.id, False)
                and procurement_bindings_are_current(
                    recommendation, bindings.get(recommendation.id, [])
                )
            )
        items.append(_approval_item(approval, recommendation, requester, decider, current))
    return ApprovalListResult(items=items, total=total, limit=limit, offset=offset)


def _target_identity(approval: Approval) -> tuple[str, UUID]:
    target_id = approval.target_id or approval.recommendation_id
    if target_id is None:
        raise ApprovalInvalidatedError("The approval target identity is missing.")
    return approval.target_type or "procurement_recommendation", target_id


def _procurement_preview_matches(approval: Approval, recommendation: Recommendation) -> bool:
    return (
        (approval.target_type or "procurement_recommendation") == "procurement_recommendation"
        and (approval.target_id is None or approval.target_id == recommendation.id)
        and approval.preview_hash == recommendation.payload_hash
        and approval.analysis_signature == recommendation.analysis_signature
        and approval.context_hash in {None, recommendation.analysis_signature}
        and recommendation.invalidated_at is None
        and recommendation.status != "invalidated"
        and approval.status not in {"invalidated", "expired"}
        and (
            not approval.preview_payload  # Historical Procurement rows predate frozen payloads.
            or payload_sha256(approval.preview_payload)[1] == approval.preview_hash
        )
    )


def _approval_item(
    approval: Approval,
    recommendation: Recommendation | None,
    requester: Actor,
    decider: Actor | None,
    current: bool,
) -> ApprovalItem:
    target_type, target_id = _target_identity(approval)
    legacy = {}
    if recommendation is not None:
        review = recommendation.impact_snapshot.get("review", {})
        product = review.get("recommended_product", {})
        impact = review.get("supplier_score", {}).get("impact", {})
        legacy = {
            "recommended_product_id": recommendation.recommended_product_id,
            "recommended_product_name": product.get("name", "Unavailable frozen product"),
            "supplier_name": product.get("supplier_name", "Unavailable frozen supplier"),
            **{
                field: impact.get(field, getattr(recommendation, field))
                for field in (
                    "projected_footprint_kgco2e",
                    "avoided_kgco2e",
                    "reduction_pct",
                    "cost_delta_pct",
                    "lead_time_delta_days",
                )
            },
        }
    return ApprovalItem(
        id=approval.id,
        company_id=approval.company_id,
        target_type=target_type,
        target_id=target_id,
        recommendation_id=approval.recommendation_id,
        context_hash=approval.context_hash,
        idempotency_key=approval.idempotency_key,
        policy_definition_id=approval.policy_definition_id,
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
        expired=_as_utc(approval.expires_at) <= datetime.now(UTC),
        preview_current=current,
        **legacy,
    )


async def get_approval(
    session: AsyncSession, *, company_id: UUID, approval_id: UUID
) -> ApprovalDetail:
    record = await repository.get_approval_record(
        session, company_id=company_id, approval_id=approval_id
    )
    if record is None:
        raise ApprovalNotFoundError("The requested approval was not found.")
    approval, recommendation, requester, decider = record
    if recommendation is None:
        current = await generic_preview_is_current(session, approval)
        payload = approval.preview_payload
    else:
        current = _procurement_preview_matches(approval, recommendation) and (
            await recommendation_preview_is_current(session, recommendation)
            and await _procurement_bindings_current(session, recommendation)
        )
        payload = approval.preview_payload
        if not payload and recommendation.ledger_event_id is not None:
            event = await repository.get_ledger_event(
                session, company_id=company_id, event_id=recommendation.ledger_event_id
            )
            if event is not None and event.payload_hash == approval.preview_hash:
                payload = event.payload
        if not payload:
            # Only expose a rebuilt legacy preview if it still hashes to the reviewed payload.
            rebuilt, digest = payload_sha256(stored_recommendation_payload(recommendation))
            payload = rebuilt if digest == approval.preview_hash else {}
    return ApprovalDetail(
        **_approval_item(approval, recommendation, requester, decider, current).model_dump(),
        preview_payload=payload or {},
    )


async def _procurement_bindings_current(
    session: AsyncSession, recommendation: Recommendation
) -> bool:
    bindings = await repository.list_fact_bindings(
        session,
        company_id=recommendation.company_id,
        artifact_type="procurement_recommendation",
        artifact_ids=[recommendation.id],
    )
    return procurement_bindings_are_current(recommendation, bindings.get(recommendation.id, []))


async def decide_approval(
    session: AsyncSession,
    *,
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    trace_id: str | None = None,
) -> ApprovalDecisionResult:
    """Serialize decisions and atomically commit target, ledger, lineage, and audit."""
    try:
        return await _decide_approval(
            session, approval_id=approval_id, request=request, trace_id=trace_id
        )
    except DBAPIError as error:
        await session.rollback()
        if getattr(error.orig, "sqlstate", None) in {"40001", "40P01"}:
            raise ApprovalConflictError(
                "The reviewed inputs changed concurrently; reload the preview before deciding."
            ) from error
        raise
    except Exception:
        await session.rollback()
        raise


async def _decide_approval(
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
        for_update=True,
    )
    if actor is None or not actor.is_active or actor.role != "approver":
        raise ApprovalActorError("An active approver must make this decision.")

    target_type, target_id = _target_identity(approval)
    if request.idempotency_key is not None and request.idempotency_key != approval.idempotency_key:
        raise ApprovalConflictError("The idempotency key does not match this approval preview.")
    if approval.status == "invalidated":
        raise ApprovalInvalidatedError("The approval preview has been invalidated.")
    if approval.status == "expired":
        raise ApprovalExpiredError("The approval preview has expired.")

    expected_status = "approved" if request.decision == "approve" else "rejected"
    if approval.status != "pending":
        if (
            approval.status == expected_status
            and approval.preview_hash == request.preview_hash
            and approval.decided_by == request.actor_id
            and approval.decision_note == request.decision_note
            and approval.decided_at is not None
            and approval.ledger_event_id is not None
        ):
            return ApprovalDecisionResult(
                approval_id=approval.id,
                recommendation_id=approval.recommendation_id,
                target_type=target_type,
                target_id=target_id,
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

    await repository.lock_review_dependencies(session, approval=approval)
    if target_type == "procurement_recommendation":
        target = await repository.get_recommendation_for_update(
            session,
            company_id=approval.company_id,
            recommendation_id=target_id,
        )
        current = (
            target is not None
            and _procurement_preview_matches(approval, target)
            and await recommendation_preview_is_current(session, target)
            and await _procurement_bindings_current(session, target)
        )
    else:
        target = await repository.get_generic_target(session, approval=approval, for_update=True)
        current = await generic_preview_is_current(session, approval, target=target)
    if target is None or target.status != "pending_approval" or not current:
        raise ApprovalInvalidatedError(
            "The reviewed payload, facts, evidence, method, forecast, or constraints changed."
        )

    decided_at = datetime.now(UTC)
    if _as_utc(approval.expires_at) <= decided_at:
        raise ApprovalExpiredError("The approval preview expired during revalidation.")
    ledger_event = await append_ledger_event(
        session,
        company_id=approval.company_id,
        event_type=f"approval.{expected_status}",
        entity_type="approval",
        entity_id=approval.id,
        payload={
            "approval_id": approval.id,
            "recommendation_id": approval.recommendation_id,
            "target_type": target_type,
            "target_id": target_id,
            "context_hash": approval.context_hash,
            "idempotency_key": approval.idempotency_key,
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
    target.status = expected_status
    if target_type == "disclosure_draft":
        target.validation_summary = {
            **target.validation_summary,
            "terminal_state": "success" if expected_status == "approved" else "policy_blocked",
        }

    if target.ledger_event_id is not None:
        await append_lineage_edge(
            session,
            company_id=approval.company_id,
            parent_event_id=target.ledger_event_id,
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
                "target_type": target_type,
                "target_id": str(target_id),
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
        target_type=target_type,
        target_id=target_id,
        status=expected_status,
        preview_hash=approval.preview_hash,
        analysis_signature=approval.analysis_signature,
        decided_by=request.actor_id,
        decided_at=decided_at,
        decision_note=request.decision_note,
        ledger_event_id=ledger_event.id,
    )
