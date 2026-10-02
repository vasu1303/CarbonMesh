"""Safe append-only recording for agent graph, tool, and provider activity."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from app.core.observability import record_event_span
from app.db.models.ai import AgentRunStep
from app.modules.agents.graph_contracts import ToolResult
from app.modules.agents.repository import (
    AgentRunRepository,
    DurableToolInvocation,
)

_REDACTED_KEYS = frozenset(
    {
        "api_key",
        "access_token",
        "authorization",
        "bearer_token",
        "body",
        "credential",
        "document_body",
        "password",
        "prompt",
        "provider_response",
        "query",
        "raw_sql",
        "refresh_token",
        "secret",
        "token",
    }
)
_MAX_STRING_LENGTH = 500
_MAX_COLLECTION_ITEMS = 100
_MAX_DEPTH = 5
MAX_DURABLE_TOOL_RESULT_BYTES = 128 * 1024
_MISSING = object()


class DurableToolResultTooLargeError(ValueError):
    """Raised before an oversized tool result can enter the replay journal."""


def durable_tool_result_snapshot(value: object) -> dict[str, object]:
    """Validate and preserve an exact replay result under a strict byte cap."""

    result = (
        value
        if isinstance(value, ToolResult)
        else ToolResult.model_validate_json(
            json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        )
    )
    snapshot = result.model_dump(mode="json")
    encoded = json.dumps(
        snapshot,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > MAX_DURABLE_TOOL_RESULT_BYTES:
        raise DurableToolResultTooLargeError(
            "durable tool result exceeds the replay-journal byte limit"
        )
    return snapshot


def safe_snapshot(value: object, *, depth: int = 0) -> object:
    """Return a JSON-safe bounded summary that excludes sensitive source bodies."""

    if depth >= _MAX_DEPTH:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (UUID, Decimal, date, datetime)):
        return str(value)
    if isinstance(value, str):
        return value[:_MAX_STRING_LENGTH]
    if isinstance(value, Mapping):
        summary: dict[str, object] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= _MAX_COLLECTION_ITEMS:
                summary["_truncated"] = True
                break
            key = str(raw_key)[:100]
            normalized = key.casefold().replace("-", "_").replace(" ", "_")
            token_secret = (
                normalized == "token"
                or normalized.endswith("_token")
                or normalized.startswith("token_")
                or "_token_" in normalized
            )
            if normalized in _REDACTED_KEYS or token_secret or any(
                marker in normalized
                for marker in (
                    "password",
                    "credential",
                    "secret",
                    "api_key",
                    "access_token",
                    "refresh_token",
                    "bearer_token",
                    "authorization",
                    "document_body",
                    "raw_sql",
                )
            ):
                summary[key] = "[redacted]"
            else:
                summary[key] = safe_snapshot(item, depth=depth + 1)
        return summary
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            safe_snapshot(item, depth=depth + 1)
            for item in value[:_MAX_COLLECTION_ITEMS]
        ]
    if hasattr(value, "model_dump"):
        return safe_snapshot(value.model_dump(mode="json"), depth=depth + 1)  # type: ignore[attr-defined]
    return str(type(value).__name__)


class AgentStepRecorder:
    def __init__(
        self,
        repository: AgentRunRepository,
        *,
        company_id: UUID,
        run_id: UUID,
    ) -> None:
        self._repository = repository
        self._company_id = company_id
        self._run_id = run_id

    async def recover_transaction(self) -> None:
        """Discard an interrupted tool transaction before writing its outcome.

        Tool handlers and the durable journal deliberately share one session so a
        successful domain mutation and its completion record commit atomically.
        The inverse matters just as much: after an exception or cancellation, no
        failure record may flush staged domain mutations.  Reloading the parent
        also refreshes its expired identity-map state after ``rollback()`` so the
        runtime can safely continue persisting checkpoints with the same session.
        """

        await self._repository.rollback()
        await self._repository.get(
            company_id=self._company_id,
            run_id=self._run_id,
        )

    async def reserve_tool_invocation(
        self,
        *,
        invocation_key: str,
        step_type: str,
        graph_name: str,
        node_name: str,
        tool_name: str,
        input_summary: Mapping[str, object],
        data: Mapping[str, object],
    ) -> DurableToolInvocation:
        """Persist dispatch intent before a tool can mutate domain state."""

        now = datetime.now(UTC)
        reserve = getattr(self._repository, "reserve_tool_invocation", None)
        if reserve is None:
            await self.record(
                step_type=step_type,
                event_name="tool.started",
                status="running",
                graph_name=graph_name,
                node_name=node_name,
                tool_name=tool_name,
                input_summary=input_summary,
                data=data,
            )
            return DurableToolInvocation(reserved=True)
        reservation = await reserve(
            company_id=self._company_id,
            run_id=self._run_id,
            invocation_key=invocation_key,
            step_type=step_type,
            graph_name=graph_name,
            node_name=node_name,
            tool_name=tool_name,
            input_snapshot=safe_snapshot(dict(input_summary)),
            output_snapshot={
                "event_name": "tool.started",
                "data": safe_snapshot(dict(data)),
            },
            started_at=now,
        )
        await self._repository.commit()
        return reservation

    async def record(
        self,
        *,
        step_type: str,
        event_name: str,
        status: str,
        data: Mapping[str, object] | None = None,
        input_summary: Mapping[str, object] | None = None,
        graph_name: str | None = None,
        node_name: str | None = None,
        tool_name: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        retry_count: int = 0,
        latency_ms: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
        commit: bool = True,
    ) -> AgentRunStep:
        now = datetime.now(UTC)
        raw_data = dict(data or {})
        durable_result = raw_data.pop("durable_result", _MISSING)
        safe_data = safe_snapshot(raw_data)
        if not isinstance(safe_data, dict):  # pragma: no cover - dict input invariant
            raise TypeError("agent step data snapshot must be an object")
        if durable_result is not _MISSING:
            # Durable replay is a validated ToolResult, not display telemetry.
            # It must remain exact (including deeply nested UUID collections),
            # while the ordinary judge-facing snapshot stays redacted/bounded.
            safe_data["durable_result"] = durable_tool_result_snapshot(durable_result)
        step = await self._repository.append_step(
            company_id=self._company_id,
            run_id=self._run_id,
            step_type=step_type,
            graph_name=graph_name,
            node_name=node_name,
            tool_name=tool_name,
            status=status,
            input_snapshot=safe_snapshot(dict(input_summary or {})),  # type: ignore[arg-type]
            output_snapshot={
                "event_name": event_name,
                "data": safe_data,
            },
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            retry_count=retry_count,
            latency_ms=latency_ms,
            started_at=now,
            completed_at=now,
            error_code=error_code,
            error_message=(error_message[:500] if error_message else None),
        )
        if commit:
            await self._repository.commit()
        record_event_span("agent." + step_type, {
            "agent.event": event_name, "agent.status": status,
            "agent.graph": graph_name or "", "agent.node": node_name or "",
            "agent.tool": tool_name or "", "agent.error_code": error_code or "",
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
        }, latency_ms)
        return step
