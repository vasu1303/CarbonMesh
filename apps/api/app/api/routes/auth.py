"""Exchange operator-provisioned demo access keys for short-lived signed sessions."""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import select

from app.api.errors import safe_http_error
from app.core.auth import (
    SESSION_COOKIE,
    Principal,
    check_origin,
    grant_for_key,
    issue_token,
    read_principal,
    require_active_principal,
)
from app.core.config import get_settings
from app.db.models.core import Actor, Company
from app.dependencies.database import DatabaseSession

router = APIRouter(prefix="/auth", tags=["authentication"])


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    access_key: SecretStr


class SessionResponse(BaseModel):
    principal: Principal
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/session", response_model=SessionResponse)
async def create_session(
    body: SessionRequest, request: Request, response: Response, session: DatabaseSession,
) -> SessionResponse:
    settings = get_settings()
    check_origin(request, settings)
    grant = grant_for_key(body.access_key.get_secret_value(), settings)
    actor = None
    if grant is not None:
        actor = await session.scalar(
            select(Actor).join(Company, Company.id == Actor.company_id).where(
                Actor.id == grant.actor_id, Actor.company_id == grant.company_id,
                Actor.is_active.is_(True), Company.is_active.is_(True),
            )
        )
    if actor is None:
        raise safe_http_error(
            trace_id=None,
            status_code=401, code="invalid_credentials",
            message="The supplied access key is invalid.", retryable=False,
        )
    principal = Principal(company_id=actor.company_id, actor_id=actor.id, role=actor.role)
    token = issue_token(principal, settings)
    response.set_cookie(
        SESSION_COOKIE, token, max_age=settings.auth_session_seconds, httponly=True,
        secure=settings.auth_cookie_secure, samesite=settings.auth_cookie_samesite, path="/api",
    )
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(
        principal=principal, access_token=token, expires_in=settings.auth_session_seconds,
    )


@router.get("/session", response_model=Principal)
async def current_session(request: Request, response: Response) -> Principal:
    response.headers["Cache-Control"] = "no-store"
    return await require_active_principal(request, read_principal(request, get_settings()))


@router.delete("/session", status_code=204)
async def end_session(request: Request, response: Response) -> None:
    check_origin(request, get_settings())
    response.delete_cookie(SESSION_COOKIE, path="/api")
    response.headers["Cache-Control"] = "no-store"
