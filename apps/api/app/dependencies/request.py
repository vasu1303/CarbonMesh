from typing import Annotated

from fastapi import Header

from app.core.tracing import TRACE_ID_PATTERN

type TraceIdHeader = Annotated[
    str | None,
    Header(alias="X-Trace-ID", max_length=100, pattern=TRACE_ID_PATTERN),
]
