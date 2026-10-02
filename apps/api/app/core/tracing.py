import re
from contextvars import ContextVar
from uuid import uuid4

TRACE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$"
current_trace_id: ContextVar[str | None] = ContextVar("carbonmesh_trace_id", default=None)


def ensure_trace_id(value: str | None) -> str:
    """Preserve a safe caller trace ID or create a correlation ID."""

    value = value or current_trace_id.get()
    return value if value and re.fullmatch(TRACE_ID_PATTERN, value) else uuid4().hex
