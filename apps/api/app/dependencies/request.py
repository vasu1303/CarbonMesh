from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request

from app.core.tracing import TRACE_ID_PATTERN

type TraceIdHeader = Annotated[
    str | None,
    Header(alias="X-Trace-ID", max_length=100, pattern=TRACE_ID_PATTERN),
]


def authenticated_actor_id(request: Request) -> UUID | None:
    """Return server-verified identity; explicit offline mode has no principal."""
    principal = getattr(request.state, "principal", None)
    return principal.actor_id if principal is not None else None


type AuthenticatedActorId = Annotated[UUID | None, Depends(authenticated_actor_id)]
