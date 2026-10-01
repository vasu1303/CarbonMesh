from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.db.models.core import Approval
from app.modules.approvals import repository as approval_repository
from app.modules.approvals.schemas import GenericApprovalPreviewRequest
from app.modules.approvals.service import (
    DEFAULT_APPROVAL_TTL,
    ApprovalActorError,
    ApprovalConflictError,
    ApprovalExpiredError,
    ApprovalPreviewValidationError,
    create_generic_approval_preview,
)
from app.modules.ledger.service import payload_sha256


class TrackingSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_count = 0
        self.commit_count = 0

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        self.flush_count += 1
        for item in self.added:
            if isinstance(item, Approval) and item.id is None:
                item.id = uuid4()

    async def commit(self) -> None:
        self.commit_count += 1
        raise AssertionError("generic preview creation must not commit")


def _request(
    *,
    company_id: UUID | None = None,
    target_id: UUID | None = None,
    requested_by: UUID | None = None,
    expires_at: datetime | None = None,
    idempotency_key: str | None = "assurance-preview:test",
) -> GenericApprovalPreviewRequest:
    company_id = company_id or uuid4()
    target_id = target_id or uuid4()
    requested_by = requested_by or uuid4()
    analysis_signature = "a" * 64
    context_hash = "b" * 64
    payload = {
        "target_type": "disclosure_draft",
        "target_id": target_id,
        "analysis_signature": analysis_signature,
        "context_hash": context_hash,
        "scope2_total": Decimal("123.450000"),
        "reviewed_at": datetime(2026, 10, 1, 10, 30, tzinfo=UTC),
    }
    _, preview_hash = payload_sha256(payload)
    return GenericApprovalPreviewRequest(
        company_id=company_id,
        target_type="disclosure_draft",
        target_id=target_id,
        requested_by=requested_by,
        preview_payload=payload,
        preview_hash=preview_hash,
        analysis_signature=analysis_signature,
        context_hash=context_hash,
        expires_at=expires_at,
        idempotency_key=idempotency_key,
    )


def _approval_from_request(
    request: GenericApprovalPreviewRequest,
    *,
    idempotency_key: str | None = None,
) -> Approval:
    normalized, _ = payload_sha256(request.preview_payload)
    return Approval(
        id=uuid4(),
        company_id=request.company_id,
        target_type=request.target_type,
        target_id=request.target_id,
        recommendation_id=None,
        requested_by=request.requested_by,
        policy_definition_id=request.policy_definition_id,
        status="pending",
        preview_payload=normalized,
        preview_hash=request.preview_hash,
        analysis_signature=request.analysis_signature,
        context_hash=request.context_hash,
        idempotency_key=idempotency_key or request.idempotency_key,
        expires_at=request.expires_at or datetime.now(UTC) + timedelta(hours=1),
    )


def _stub_repository(
    monkeypatch: pytest.MonkeyPatch,
    *,
    actor: object | None = None,
    by_key: Approval | None = None,
    by_target: Approval | None = None,
) -> None:
    async def get_actor(_session, **_):
        return actor if actor is not None else SimpleNamespace(is_active=True)

    async def get_by_key(_session, **_):
        return by_key

    async def get_by_target(_session, **_):
        return by_target

    monkeypatch.setattr(approval_repository, "get_actor", get_actor)
    monkeypatch.setattr(
        approval_repository,
        "get_approval_by_idempotency_key",
        get_by_key,
    )
    monkeypatch.setattr(
        approval_repository,
        "get_pending_approval_for_target",
        get_by_target,
    )


def test_generic_preview_request_is_strict_and_requires_aware_expiry() -> None:
    request = _request()

    with pytest.raises(ValidationError, match="extra_forbidden"):
        GenericApprovalPreviewRequest.model_validate({**request.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError, match="UTC offset"):
        GenericApprovalPreviewRequest.model_validate(
            {
                **request.model_dump(),
                "expires_at": datetime(2026, 10, 1, 12, 0, tzinfo=UTC).replace(tzinfo=None),
            }
        )
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        GenericApprovalPreviewRequest.model_validate(
            {**request.model_dump(), "analysis_signature": "not-a-hash"}
        )


@pytest.mark.asyncio
async def test_generic_preview_canonicalizes_and_flushes_without_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(idempotency_key=None)
    _stub_repository(monkeypatch)
    session = TrackingSession()
    started_at = datetime.now(UTC)

    result = await create_generic_approval_preview(session, request)  # type: ignore[arg-type]

    assert result.idempotent_replay is False
    assert result.preview_payload["target_id"] == str(request.target_id)
    assert result.preview_payload["scope2_total"] == "123.450000"
    assert result.preview_hash == request.preview_hash
    assert result.expires_at >= started_at + DEFAULT_APPROVAL_TTL - timedelta(seconds=1)
    expected_key = hashlib.sha256(
        (
            f"approval:{request.company_id}:{request.target_type}:"
            f"{request.target_id}:{request.preview_hash}"
        ).encode()
    ).hexdigest()
    assert result.idempotency_key == expected_key
    assert session.flush_count == 1
    assert session.commit_count == 0
    assert len(session.added) == 1


@pytest.mark.asyncio
async def test_generic_preview_rejects_hash_or_payload_binding_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_repository(monkeypatch)
    session = TrackingSession()
    request = _request()

    with pytest.raises(ApprovalPreviewValidationError, match="preview hash"):
        await create_generic_approval_preview(  # type: ignore[arg-type]
            session,
            request.model_copy(update={"preview_hash": "c" * 64}),
        )

    mismatched_payload = {**request.preview_payload, "target_id": str(uuid4())}
    _, mismatched_hash = payload_sha256(mismatched_payload)
    with pytest.raises(ApprovalPreviewValidationError, match="not bound"):
        await create_generic_approval_preview(  # type: ignore[arg-type]
            session,
            request.model_copy(
                update={
                    "preview_payload": mismatched_payload,
                    "preview_hash": mismatched_hash,
                }
            ),
        )

    assert session.flush_count == 0


@pytest.mark.asyncio
async def test_generic_preview_requires_active_tenant_actor_and_future_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    session = TrackingSession()
    _stub_repository(monkeypatch, actor=SimpleNamespace(is_active=False))

    with pytest.raises(ApprovalActorError, match="active actor"):
        await create_generic_approval_preview(session, request)  # type: ignore[arg-type]

    _stub_repository(monkeypatch)
    with pytest.raises(ApprovalExpiredError, match="future"):
        await create_generic_approval_preview(session, request)  # type: ignore[arg-type]

    assert session.flush_count == 0


@pytest.mark.asyncio
async def test_generic_preview_replays_exact_idempotency_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(expires_at=datetime.now(UTC) + timedelta(hours=2))
    existing = _approval_from_request(request)
    _stub_repository(monkeypatch, by_key=existing)
    session = TrackingSession()

    result = await create_generic_approval_preview(session, request)  # type: ignore[arg-type]

    assert result.approval_id == existing.id
    assert result.idempotent_replay is True
    assert session.added == []
    assert session.flush_count == 0


@pytest.mark.asyncio
async def test_generic_preview_rejects_idempotency_or_pending_target_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    conflicting = _approval_from_request(request)
    conflicting.preview_hash = "f" * 64
    session = TrackingSession()

    _stub_repository(monkeypatch, by_key=conflicting)
    with pytest.raises(ApprovalConflictError, match="idempotency key"):
        await create_generic_approval_preview(session, request)  # type: ignore[arg-type]

    _stub_repository(monkeypatch, by_target=conflicting)
    with pytest.raises(ApprovalConflictError, match="pending preview"):
        await create_generic_approval_preview(session, request)  # type: ignore[arg-type]

    assert session.flush_count == 0
