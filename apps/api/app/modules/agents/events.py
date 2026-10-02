from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from time import monotonic
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.ai import AgentRunStep
from app.modules.agents.repository import AgentRunRepository
from app.modules.agents.schemas import AgentEvent, AgentTelemetry

_SSE_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")
_SSE_STEP_PAGE_SIZE = 100
_BLOCKING_INTERRUPT_EVENTS = frozenset(
    {"approval.required", "clarification.required"}
)


def encode_sse_event(event: AgentEvent) -> str:
    """Encode one named event without placing unescaped user text in SSE fields."""
    event_payload = {
        "occurred_at": event.occurred_at.isoformat(),
        **event.data,
    }
    payload = json.dumps(
        event_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return f"id: {event.sequence}\nevent: {event.name}\ndata: {payload}\n\n"


def encode_sse_step(step: AgentRunStep) -> str | None:
    """Map one persisted step's safe event snapshot to the SSE wire format."""

    snapshot = step.output_snapshot
    if not isinstance(snapshot, dict):
        return None
    event_name = snapshot.get("event_name")
    data = snapshot.get("data")
    if (
        not isinstance(event_name, str)
        or _SSE_EVENT_NAME.fullmatch(event_name) is None
        or not isinstance(data, dict)
    ):
        return None

    occurred_at = step.completed_at or step.started_at or step.created_at
    event_payload = {
        **data,
        "sequence": step.sequence,
        "run_id": str(step.agent_run_id),
        "step_type": step.step_type,
        "status": step.status,
        "graph_name": step.graph_name,
        "node_name": step.node_name,
        "tool_name": step.tool_name,
        "input_tokens": step.input_tokens,
        "output_tokens": step.output_tokens,
        "retry_count": step.retry_count,
        "latency_ms": step.latency_ms,
        "error_code": step.error_code,
    }
    if occurred_at is not None:
        event_payload["occurred_at"] = occurred_at.isoformat()
    payload = json.dumps(
        event_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return f"id: {step.sequence}\nevent: {event_name}\ndata: {payload}\n\n"


async def follow_persisted_sse_events(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    company_id: UUID,
    run_id: UUID,
    last_event_id: int | None = None,
    poll_interval_seconds: float = 0.1,
    heartbeat_interval_seconds: float = 15.0,
) -> AsyncIterator[str]:
    """Replay committed steps, falling back to legacy telemetry-only runs."""

    cursor = last_event_id or 0
    last_activity = monotonic()
    while True:
        async with session_factory() as session:
            repository = AgentRunRepository(session)
            run = await repository.get(company_id=company_id, run_id=run_id)
            if run is None:
                return

            steps = await repository.list_steps_after(
                company_id=company_id,
                run_id=run_id,
                after_sequence=cursor,
                limit=_SSE_STEP_PAGE_SIZE,
            )
            has_persisted_steps = bool(steps) or await repository.has_steps(
                company_id=company_id,
                run_id=run_id,
            )
            legacy_events: tuple[AgentEvent, ...] = ()
            active_interrupt = False
            if has_persisted_steps and run.terminal_state == "running":
                latest_interrupt = await repository.latest_interrupt(
                    company_id=company_id,
                    run_id=run_id,
                )
                if latest_interrupt is not None:
                    snapshot = latest_interrupt.output_snapshot
                    event_name = (
                        snapshot.get("event_name")
                        if isinstance(snapshot, dict)
                        else None
                    )
                    if event_name in _BLOCKING_INTERRUPT_EVENTS:
                        active_interrupt = not await repository.list_steps_after(
                            company_id=company_id,
                            run_id=run_id,
                            after_sequence=latest_interrupt.sequence,
                            limit=1,
                        )
            elif not has_persisted_steps:
                telemetry = AgentTelemetry.model_validate(run.telemetry)
                legacy_events = tuple(
                    event for event in telemetry.events if event.sequence > cursor
                )
            terminal = run.terminal_state != "running"

        if has_persisted_steps:
            for step in steps:
                encoded = encode_sse_step(step)
                cursor = step.sequence
                if encoded is not None:
                    yield encoded
                    last_activity = monotonic()
        else:
            for event in legacy_events:
                yield encode_sse_event(event)
                cursor = event.sequence
                last_activity = monotonic()

        if (terminal or active_interrupt) and len(steps) < _SSE_STEP_PAGE_SIZE:
            return
        if steps:
            continue
        if monotonic() - last_activity >= heartbeat_interval_seconds:
            yield ": heartbeat\n\n"
            last_activity = monotonic()
        await asyncio.sleep(poll_interval_seconds)
