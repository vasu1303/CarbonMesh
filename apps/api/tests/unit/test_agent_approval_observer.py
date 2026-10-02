from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agents import resume
from app.modules.approvals.service import ApprovalNotFoundError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "status", "code"),
    [
        ({}, "approved", "approval_approved"),
        ({"status": "pending"}, "pending", "approval_pending"),
        ({"status": "rejected"}, "rejected", "approval_rejected"),
        ({"preview_hash": "b" * 64}, "stale", "approval_resume_mismatch"),
        ({"analysis_signature": "b" * 64}, "stale", "approval_resume_mismatch"),
        ({"context_hash": None}, "stale", "approval_resume_mismatch"),
        ({"target_type": "different"}, "stale", "approval_resume_mismatch"),
        ({"target_id": uuid4()}, "stale", "approval_resume_mismatch"),
        ({"expired": True}, "stale", "approval_expired"),
        ({"status": "expired"}, "stale", "approval_expired"),
        ({"preview_current": False}, "stale", "approval_target_changed"),
        ({"status": "invalidated"}, "stale", "approval_target_changed"),
        ({"decided_by": None}, "stale", "approval_decision_incomplete"),
        ({"decided_at": None}, "stale", "approval_decision_incomplete"),
        ({"ledger_event_id": None}, "stale", "approval_decision_incomplete"),
    ],
)
async def test_observer_revalidates_exact_preview_and_durable_decision(
    monkeypatch, changes, status, code
) -> None:
    company_id, approval_id, target_id = uuid4(), uuid4(), uuid4()
    session = object()
    detail = SimpleNamespace(
        status="approved",
        preview_hash="a" * 64,
        analysis_signature="c" * 64,
        context_hash="d" * 64,
        target_type="disclosure_draft",
        target_id=target_id,
        expired=False,
        preview_current=True,
        decided_by=uuid4(),
        decided_at=datetime.now(UTC),
        ledger_event_id=uuid4(),
    )
    for key, value in changes.items():
        setattr(detail, key, value)

    async def get_approval(received_session, **scope):
        assert received_session is session
        assert scope == {"company_id": company_id, "approval_id": approval_id}
        return detail

    monkeypatch.setattr(resume, "get_approval", get_approval)
    observed = await resume.GenericApprovalResumeObserver(session).validate(
        company_id=company_id,
        approval_id=approval_id,
        preview_hash="a" * 64,
        analysis_signature="c" * 64,
        context_hash="d" * 64,
        target_type="disclosure_draft",
        target_id=target_id,
    )
    assert observed == resume.ApprovalResumeState(status, code)


@pytest.mark.asyncio
async def test_observer_missing_tenant_scoped_approval_fails_closed(monkeypatch) -> None:
    async def missing_approval(*_args, **_kwargs):
        raise ApprovalNotFoundError("Approval not found in this tenant.")

    monkeypatch.setattr(resume, "get_approval", missing_approval)
    observed = await resume.GenericApprovalResumeObserver(object()).validate(
        company_id=uuid4(),
        approval_id=uuid4(),
        preview_hash="a" * 64,
        analysis_signature="b" * 64,
    )
    assert observed == resume.ApprovalResumeState("stale", "approval_not_found")
