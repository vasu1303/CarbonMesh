"""Small server-owned demo identity boundary, shared by REST and cookie-based SSE."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Literal
from uuid import UUID

from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError
from sqlalchemy import select

from app.api.errors import safe_http_error
from app.core.config import Settings, get_settings
from app.db.models.core import Actor, Company
from app.dependencies.database import get_db_session_factory

SESSION_COOKIE = "carbonmesh_session"
AUTH_SALT = "carbonmesh.demo-session.v1"
PUBLIC_PATHS = frozenset({"/api/health", "/api/health/ready", "/api/auth/session"})


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    company_id: UUID
    actor_id: UUID
    role: Literal["sustainability_analyst", "procurement_manager", "approver", "auditor", "system"]


class AccessGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    key: SecretStr
    company_id: UUID
    actor_id: UUID


def access_grants(settings: Settings) -> list[AccessGrant]:
    """Invalid credentials disable authentication; their contents never enter an error."""
    try:
        if settings.auth_access_keys is None or settings.auth_signing_key is None:
            raise ValueError
        if len(settings.auth_signing_key.get_secret_value()) < 32:
            raise ValueError
        raw = json.loads(settings.auth_access_keys.get_secret_value())
        if not isinstance(raw, list) or not 1 <= len(raw) <= 50:
            raise ValueError
        grants = [AccessGrant.model_validate(item) for item in raw]
        keys = [item.key.get_secret_value() for item in grants]
        if any(len(key) < 32 for key in keys) or len(set(keys)) != len(keys):
            raise ValueError
        return grants
    except (ValueError, TypeError, ValidationError):
        raise safe_http_error(
            trace_id=None,
            status_code=503, code="authentication_not_configured",
            message="Server authentication is not configured.", retryable=False,
        ) from None


def serializer(settings: Settings) -> URLSafeTimedSerializer:
    if settings.auth_signing_key is None:
        access_grants(settings)
    assert settings.auth_signing_key is not None
    return URLSafeTimedSerializer(
        settings.auth_signing_key.get_secret_value(), salt=AUTH_SALT,
        signer_kwargs={"digest_method": hashlib.sha256},
    )


def grant_for_key(key: str, settings: Settings) -> AccessGrant | None:
    matched = None
    for grant in access_grants(settings):
        if secrets.compare_digest(key.encode(), grant.key.get_secret_value().encode()):
            matched = grant
    return matched


def issue_token(principal: Principal, settings: Settings) -> str:
    return serializer(settings).dumps(principal.model_dump(mode="json"))


def read_principal(request: Request, settings: Settings) -> Principal:
    grants = access_grants(settings)
    authorization = request.headers.get("authorization", "")
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not token:
            token = ""
    else:
        token = request.cookies.get(SESSION_COOKIE, "")
    try:
        if not token or len(token) > 4096:
            raise ValueError
        principal = Principal.model_validate(
            serializer(settings).loads(token, max_age=settings.auth_session_seconds)
        )
        if not any(
            grant.actor_id == principal.actor_id and grant.company_id == principal.company_id
            for grant in grants
        ):
            raise ValueError
        return principal
    except (BadSignature, ValueError, TypeError, ValidationError):
        raise safe_http_error(
            trace_id=None,
            status_code=401, code="authentication_required",
            message="A valid session is required.", retryable=False,
        ) from None


def check_origin(request: Request, settings: Settings) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    allowed = {str(request.base_url).rstrip("/"), *settings.cors_origins}
    if origin is not None and origin.rstrip("/") not in allowed:
        raise safe_http_error(
            trace_id=None,
            status_code=403, code="origin_forbidden",
            message="This request origin is not allowed.", retryable=False,
        )


async def require_active_principal(request: Request, principal: Principal) -> Principal:
    """Revocation and role changes take effect on the next request, including SSE reconnect."""
    provider = request.app.dependency_overrides.get(
        get_db_session_factory, get_db_session_factory,
    )
    session_factory = provider()
    async with session_factory() as session:
        actor = await session.scalar(select(Actor).join(Company).where(
            Actor.id == principal.actor_id, Actor.company_id == principal.company_id,
            Actor.is_active.is_(True), Company.is_active.is_(True), Actor.role == principal.role,
        ))
    if actor is None:
        raise safe_http_error(
            trace_id=request.headers.get("X-Trace-ID"), status_code=401,
            code="session_revoked", message="This session is no longer authorized.",
            retryable=False,
        )
    return principal


def _check_identity(payload: object, principal: Principal) -> None:
    if not isinstance(payload, dict):
        return
    expected = {
        "company_id": str(principal.company_id),
        "actor_id": str(principal.actor_id),
        "requested_by": str(principal.actor_id),
    }
    for field, value in expected.items():
        if payload.get(field) is not None and str(payload[field]) != value:
            raise safe_http_error(
                trace_id=None,
                status_code=403, code="identity_mismatch",
                message="Request identity does not match the authenticated principal.",
                retryable=False,
            )
    # Only contract envelopes are identity selectors. Evidence metadata is untrusted data.
    for name in ("context", "clarification", "approval"):
        if name in payload:
            _check_identity(payload[name], principal)


async def authorize_request(request: Request) -> None:
    settings = get_settings()
    if not settings.auth_required or request.url.path in PUBLIC_PATHS:
        return
    principal = await require_active_principal(request, read_principal(request, settings))
    request.state.principal = principal
    check_origin(request, settings)
    for field in ("company_id", "actor_id", "requested_by"):
        for value in request.query_params.getlist(field):
            _check_identity({field: value}, principal)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if principal.role == "auditor":
            raise safe_http_error(
                trace_id=None,
                status_code=403, code="role_forbidden",
                message="The authenticated role cannot perform this action.", retryable=False,
            )
        if request.url.path.endswith("/decision") and principal.role != "approver":
            raise safe_http_error(
                trace_id=None,
                status_code=403, code="approver_required",
                message="An authenticated approver is required.", retryable=False,
            )
        try:
            payload = await request.json()
        except (ValueError, UnicodeError):
            return  # Ordinary route validation supplies the canonical malformed-body response.
        _check_identity(payload, principal)
