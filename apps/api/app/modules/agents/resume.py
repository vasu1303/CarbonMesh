"""Fail-closed approval state revalidation for durable graph resume."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.approvals.service import ApprovalNotFoundError, get_approval

ApprovalResumeStatus = Literal["pending", "approved", "rejected", "stale"]


@dataclass(frozen=True, slots=True)
class ApprovalResumeState:
    status: ApprovalResumeStatus
    code: str


class ApprovalResumePort(Protocol):
    """Read-only approval observation through the shared approval service."""

    async def validate(
        self,
        *,
        company_id: UUID,
        approval_id: UUID,
        preview_hash: str,
        analysis_signature: str,
        context_hash: str | None = None,
        target_type: str | None = None,
        target_id: UUID | None = None,
    ) -> ApprovalResumeState: ...


class GenericApprovalResumeObserver:
    """Revalidate persisted human decisions without authorizing or committing them."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def validate(
        self,
        *,
        company_id: UUID,
        approval_id: UUID,
        preview_hash: str,
        analysis_signature: str,
        context_hash: str | None = None,
        target_type: str | None = None,
        target_id: UUID | None = None,
    ) -> ApprovalResumeState:
        try:
            approval = await get_approval(
                self._session, company_id=company_id, approval_id=approval_id
            )
        except ApprovalNotFoundError:
            return ApprovalResumeState("stale", "approval_not_found")
        if (
            approval.preview_hash != preview_hash
            or approval.analysis_signature != analysis_signature
            or (context_hash is not None and approval.context_hash != context_hash)
            or (target_type is not None and approval.target_type != target_type)
            or (target_id is not None and approval.target_id != target_id)
        ):
            return ApprovalResumeState("stale", "approval_resume_mismatch")
        if approval.expired or approval.status == "expired":
            return ApprovalResumeState("stale", "approval_expired")
        if not approval.preview_current or approval.status == "invalidated":
            return ApprovalResumeState("stale", "approval_target_changed")
        if approval.status == "pending":
            return ApprovalResumeState("pending", "approval_pending")
        if (
            approval.decided_by is None
            or approval.decided_at is None
            or approval.ledger_event_id is None
        ):
            return ApprovalResumeState("stale", "approval_decision_incomplete")
        if approval.status == "approved":
            return ApprovalResumeState("approved", "approval_approved")
        return ApprovalResumeState("rejected", "approval_rejected")
