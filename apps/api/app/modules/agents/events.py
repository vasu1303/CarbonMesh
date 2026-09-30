from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.agents.repository import AgentRunRepository
from app.modules.agents.schemas import AgentEvent, AgentTelemetry


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


async def follow_persisted_sse_events(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    company_id: UUID,
    run_id: UUID,
    last_event_id: int | None = None,
    poll_interval_seconds: float = 0.1,
) -> AsyncIterator[str]:
    """Follow committed event snapshots until the persisted run reaches a terminal state."""

    cursor = last_event_id or 0
    while True:
        async with session_factory() as session:
            run = await AgentRunRepository(session).get(company_id=company_id, run_id=run_id)
            if run is None:
                return
            telemetry = AgentTelemetry.model_validate(run.telemetry)
            terminal = run.terminal_state != "running"

        for event in telemetry.events:
            if event.sequence > cursor:
                yield encode_sse_event(event)
                cursor = event.sequence

        if terminal:
            return
        await asyncio.sleep(poll_interval_seconds)
