from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar
from uuid import uuid4

import pytest

from app.db.models.ai import AgentRunStep
from app.modules.agents import events as agent_events
from app.modules.agents.events import encode_sse_step, follow_persisted_sse_events
from app.modules.agents.repository import (
    MAX_AGENT_RUN_STEP_PAGE_SIZE,
    AgentRunRepository,
    AgentRunStepParentNotFoundError,
)
from app.modules.agents.schemas import AgentEvent
from app.modules.agents.step_recorder import safe_snapshot


class ScalarCollection:
    def __init__(self, values: list[AgentRunStep]) -> None:
        self._values = values

    def all(self) -> list[AgentRunStep]:
        return self._values


class RepositorySession:
    def __init__(
        self,
        *,
        scalar_values: list[object] | None = None,
        step_pages: list[list[AgentRunStep]] | None = None,
    ) -> None:
        self.scalar_values = list(scalar_values or [])
        self.step_pages = list(step_pages or [])
        self.scalar_statements: list[object] = []
        self.scalars_statements: list[object] = []
        self.added: list[object] = []
        self.flush_count = 0

    async def scalar(self, statement):
        self.scalar_statements.append(statement)
        return self.scalar_values.pop(0)

    async def scalars(self, statement):
        self.scalars_statements.append(statement)
        return ScalarCollection(self.step_pages.pop(0))

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1


def _step(
    sequence: int,
    *,
    step_type: str = "node",
    event_name: str = "node.completed",
) -> AgentRunStep:
    occurred_at = datetime(2026, 10, 1, 12, 0, sequence, tzinfo=UTC)
    return AgentRunStep(
        id=uuid4(),
        company_id=uuid4(),
        agent_run_id=uuid4(),
        sequence=sequence,
        step_type=step_type,
        status="completed",
        input_snapshot={},
        output_snapshot={"event_name": event_name, "data": {"sequence_value": sequence}},
        input_tokens=0,
        output_tokens=0,
        retry_count=0,
        latency_ms=1,
        started_at=occurred_at,
        completed_at=occurred_at,
        created_at=occurred_at,
    )


def test_safe_snapshot_redacts_credential_bearing_fields() -> None:
    snapshot = safe_snapshot(
        {
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "bearer_token": "bearer-secret",
            "oauth_access_token_value": "nested-access-secret",
            "authorization_header": "Bearer hidden",
            "x-api-key": "api-secret",
            "client-secret": "client-secret",
            "id-token": "identity-secret",
            "input_tokens": 42,
        }
    )

    assert snapshot == {
        "access_token": "[redacted]",
        "refresh_token": "[redacted]",
        "bearer_token": "[redacted]",
        "oauth_access_token_value": "[redacted]",
        "authorization_header": "[redacted]",
        "x-api-key": "[redacted]",
        "client-secret": "[redacted]",
        "id-token": "[redacted]",
        "input_tokens": 42,
    }


@pytest.mark.asyncio
async def test_append_step_locks_tenant_run_before_allocating_sequence() -> None:
    company_id = uuid4()
    run_id = uuid4()
    inserted_step = _step(8, step_type="tool", event_name="tool.completed")
    inserted_step.company_id = company_id
    inserted_step.agent_run_id = run_id
    session = RepositorySession(scalar_values=[run_id, inserted_step])
    repository = AgentRunRepository(session)  # type: ignore[arg-type]

    step = await repository.append_step(
        company_id=company_id,
        run_id=run_id,
        step_type="tool",
        status="completed",
        tool_name="resolve_context",
        input_snapshot={"safe": "input"},
        output_snapshot={"event_name": "tool.completed", "data": {"rows": 1}},
    )

    assert step.sequence == 8
    assert step.company_id == company_id
    assert step.agent_run_id == run_id
    assert step is inserted_step
    assert session.added == []
    assert session.flush_count == 1
    assert len(session.scalar_statements) == 2
    assert "FOR UPDATE" in str(session.scalar_statements[0]).upper()
    assert "max(ai.agent_run_steps.sequence)" in str(session.scalar_statements[1])
    assert "INSERT INTO ai.agent_run_steps" in str(session.scalar_statements[1])
    assert "RETURNING" in str(session.scalar_statements[1])


@pytest.mark.asyncio
async def test_append_step_rejects_unknown_or_cross_tenant_run() -> None:
    session = RepositorySession(scalar_values=[None])
    repository = AgentRunRepository(session)  # type: ignore[arg-type]

    with pytest.raises(AgentRunStepParentNotFoundError):
        await repository.append_step(
            company_id=uuid4(),
            run_id=uuid4(),
            step_type="node",
            status="running",
        )

    assert session.added == []
    assert len(session.scalar_statements) == 1


@pytest.mark.asyncio
async def test_list_steps_after_is_bounded_tenant_scoped_and_ordered() -> None:
    steps = [_step(3), _step(4)]
    session = RepositorySession(step_pages=[steps])
    repository = AgentRunRepository(session)  # type: ignore[arg-type]

    result = await repository.list_steps_after(
        company_id=uuid4(),
        run_id=uuid4(),
        after_sequence=2,
        limit=20,
    )

    assert result == steps
    statement = str(session.scalars_statements[0])
    assert "ai.agent_run_steps.company_id" in statement
    assert "ai.agent_run_steps.agent_run_id" in statement
    assert "ai.agent_run_steps.sequence >" in statement
    assert "ORDER BY ai.agent_run_steps.sequence" in statement

    with pytest.raises(ValueError, match="after_sequence"):
        await repository.list_steps_after(
            company_id=uuid4(), run_id=uuid4(), after_sequence=-1
        )
    with pytest.raises(ValueError, match="limit"):
        await repository.list_steps_after(
            company_id=uuid4(),
            run_id=uuid4(),
            limit=MAX_AGENT_RUN_STEP_PAGE_SIZE + 1,
        )


@pytest.mark.asyncio
async def test_latest_interrupt_is_tenant_scoped_and_descending() -> None:
    interrupt = _step(5, step_type="interrupt", event_name="approval.required")
    session = RepositorySession(scalar_values=[interrupt])
    repository = AgentRunRepository(session)  # type: ignore[arg-type]

    result = await repository.latest_interrupt(company_id=uuid4(), run_id=uuid4())

    assert result is interrupt
    statement = str(session.scalar_statements[0])
    assert "ai.agent_run_steps.step_type" in statement
    assert "ORDER BY ai.agent_run_steps.sequence DESC" in statement


def test_encode_sse_step_uses_stable_snapshot_mapping() -> None:
    step = _step(2, event_name="provider.completed")
    step.graph_name = "measurement"
    step.node_name = "measurement.calculate"
    step.tool_name = "calculate_emissions"
    step.input_tokens = 3
    step.output_tokens = 5
    step.retry_count = 1
    step.latency_ms = 7
    step.error_code = "provider_timeout"
    step.output_snapshot["data"].update({"status": "forged", "latency_ms": 999})

    encoded = encode_sse_step(step)

    assert encoded is not None
    assert encoded.startswith("id: 2\nevent: provider.completed\n")
    payload = json.loads(encoded.split("data: ", maxsplit=1)[1])
    assert payload == {
        "error_code": "provider_timeout",
        "graph_name": "measurement",
        "input_tokens": 3,
        "latency_ms": 7,
        "node_name": "measurement.calculate",
        "occurred_at": "2026-10-01T12:00:02+00:00",
        "output_tokens": 5,
        "retry_count": 1,
        "run_id": str(step.agent_run_id),
        "sequence": 2,
        "sequence_value": 2,
        "status": "completed",
        "step_type": "node",
        "tool_name": "calculate_emissions",
    }

    invalid = _step(3)
    invalid.output_snapshot = {"event_name": "bad\nevent", "data": {}}
    assert encode_sse_step(invalid) is None


class StreamSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class StreamSessionFactory:
    def __call__(self) -> StreamSession:
        return StreamSession()


class StepStreamRepository:
    run = SimpleNamespace(
        terminal_state="success",
        telemetry={
            "trace_id": "legacy-trace",
            "analysis_signature": "a" * 64,
            "elapsed_ms": 1,
            "events": [
                AgentEvent(
                    sequence=99,
                    name="run.completed",
                    occurred_at="2026-10-01T12:00:00Z",
                    data={"legacy": True},
                ).model_dump(mode="json")
            ],
        },
    )
    steps: ClassVar[list[AgentRunStep]] = [
        _step(1, event_name="run.started"),
        _step(2, event_name="run.completed"),
    ]

    def __init__(self, _) -> None:
        pass

    async def get(self, **_) -> object:
        return self.run

    async def list_steps_after(
        self,
        *,
        after_sequence: int,
        limit: int,
        **_,
    ) -> list[AgentRunStep]:
        return [step for step in self.steps if step.sequence > after_sequence][:limit]

    async def has_steps(self, **_) -> bool:
        return True

    async def latest_interrupt(self, **_) -> AgentRunStep | None:
        return None


@pytest.mark.asyncio
async def test_sse_prefers_steps_and_honors_last_event_id(monkeypatch) -> None:
    monkeypatch.setattr(agent_events, "AgentRunRepository", StepStreamRepository)

    chunks = [
        chunk
        async for chunk in follow_persisted_sse_events(
            session_factory=StreamSessionFactory(),  # type: ignore[arg-type]
            company_id=uuid4(),
            run_id=uuid4(),
            last_event_id=1,
            poll_interval_seconds=0,
        )
    ]

    assert len(chunks) == 1
    assert "id: 2\n" in chunks[0]
    assert "event: run.completed\n" in chunks[0]
    assert "legacy" not in chunks[0]


class LegacyStreamRepository(StepStreamRepository):
    steps: ClassVar[list[AgentRunStep]] = []

    async def has_steps(self, **_) -> bool:
        return False


@pytest.mark.asyncio
async def test_sse_falls_back_to_legacy_telemetry_when_no_steps_exist(monkeypatch) -> None:
    monkeypatch.setattr(agent_events, "AgentRunRepository", LegacyStreamRepository)

    chunks = [
        chunk
        async for chunk in follow_persisted_sse_events(
            session_factory=StreamSessionFactory(),  # type: ignore[arg-type]
            company_id=uuid4(),
            run_id=uuid4(),
            poll_interval_seconds=0,
        )
    ]

    assert len(chunks) == 1
    assert "id: 99\n" in chunks[0]
    assert "event: run.completed\n" in chunks[0]
    assert '"legacy":true' in chunks[0]


class InterruptStreamRepository(StepStreamRepository):
    interrupt = _step(1, step_type="interrupt", event_name="clarification.required")
    steps: ClassVar[list[AgentRunStep]] = [interrupt]
    run = SimpleNamespace(terminal_state="running", telemetry={})

    async def latest_interrupt(self, **_) -> AgentRunStep | None:
        return self.interrupt


@pytest.mark.asyncio
async def test_sse_closes_when_latest_persisted_step_is_an_interrupt(monkeypatch) -> None:
    monkeypatch.setattr(agent_events, "AgentRunRepository", InterruptStreamRepository)

    chunks = [
        chunk
        async for chunk in follow_persisted_sse_events(
            session_factory=StreamSessionFactory(),  # type: ignore[arg-type]
            company_id=uuid4(),
            run_id=uuid4(),
            poll_interval_seconds=0,
        )
    ]

    assert len(chunks) == 1
    assert "event: clarification.required\n" in chunks[0]


class WaitingStreamRepository(StepStreamRepository):
    steps: ClassVar[list[AgentRunStep]] = []
    run = SimpleNamespace(terminal_state="running", telemetry={})

    async def has_steps(self, **_) -> bool:
        return True

    async def latest_interrupt(self, **_) -> AgentRunStep | None:
        return None


@pytest.mark.asyncio
async def test_sse_emits_comment_heartbeat_while_waiting_for_persisted_steps(
    monkeypatch,
) -> None:
    monkeypatch.setattr(agent_events, "AgentRunRepository", WaitingStreamRepository)
    stream = follow_persisted_sse_events(
        session_factory=StreamSessionFactory(),  # type: ignore[arg-type]
        company_id=uuid4(),
        run_id=uuid4(),
        poll_interval_seconds=0,
        heartbeat_interval_seconds=0,
    )
    try:
        assert await anext(stream) == ": heartbeat\n\n"
    finally:
        await stream.aclose()


class ResumedStreamRepository(StepStreamRepository):
    required = _step(1, step_type="interrupt", event_name="clarification.required")
    resumed = _step(2, step_type="interrupt", event_name="run.resumed")
    steps: ClassVar[list[AgentRunStep]] = [required, resumed]
    run = SimpleNamespace(terminal_state="running", telemetry={})

    async def latest_interrupt(self, **_) -> AgentRunStep | None:
        return self.resumed


@pytest.mark.asyncio
async def test_sse_keeps_following_after_nonblocking_resume_interrupt_step(
    monkeypatch,
) -> None:
    monkeypatch.setattr(agent_events, "AgentRunRepository", ResumedStreamRepository)
    stream = follow_persisted_sse_events(
        session_factory=StreamSessionFactory(),  # type: ignore[arg-type]
        company_id=uuid4(),
        run_id=uuid4(),
        last_event_id=1,
        poll_interval_seconds=0,
        heartbeat_interval_seconds=0,
    )
    try:
        resumed = await anext(stream)
        assert "id: 2\n" in resumed
        assert "event: run.resumed\n" in resumed
        assert await anext(stream) == ": heartbeat\n\n"
    finally:
        await stream.aclose()
