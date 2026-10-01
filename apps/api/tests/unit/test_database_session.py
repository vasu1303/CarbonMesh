from __future__ import annotations

import ssl

import pytest
from pydantic import SecretStr
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.db import session as database_session
from app.db.session import (
    DatabaseConfigurationError,
    get_database_target,
    normalize_database_url,
)


def test_settings_redact_database_url() -> None:
    secret = "postgresql://owner:do-not-print@example.test/carbonmesh?sslmode=require"

    settings = Settings(database_url=secret)

    assert isinstance(settings.database_url, SecretStr)
    assert "do-not-print" not in repr(settings)
    assert "do-not-print" not in str(settings.database_url)


def test_normalize_neon_url_for_asyncpg_and_tls() -> None:
    secret = (
        "postgresql://owner:do-not-print@ep-example-pooler.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require&application_name=carbonmesh"
    )

    configuration = normalize_database_url(SecretStr(secret))

    assert configuration.url.drivername == "postgresql+asyncpg"
    assert configuration.url.host == "ep-example-pooler.aws.neon.tech"
    assert "sslmode" not in configuration.url.query
    assert "channel_binding" not in configuration.url.query
    assert configuration.url.query["application_name"] == "carbonmesh"
    assert configuration.url.query["prepared_statement_cache_size"] == "0"
    tls_context = configuration.connect_args["ssl"]
    assert isinstance(tls_context, ssl.SSLContext)
    assert tls_context.check_hostname
    assert tls_context.verify_mode == ssl.CERT_REQUIRED
    assert tls_context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert configuration.connect_args["statement_cache_size"] == 0
    assert configuration.is_neon
    assert configuration.uses_tls
    assert "do-not-print" not in str(configuration.url)
    assert "do-not-print" not in repr(configuration)


def test_normalize_remote_url_rejects_insecure_tls_without_disclosing_secret() -> None:
    secret = "postgresql://owner:do-not-print@example.test/carbonmesh?sslmode=disable"

    try:
        normalize_database_url(secret)
    except DatabaseConfigurationError as error:
        assert "must use sslmode=require" in str(error)
        assert "do-not-print" not in str(error)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("An insecure database URL must be rejected.")


def test_normalize_local_url_allows_explicit_non_tls_connection() -> None:
    configuration = normalize_database_url(
        "postgresql://carbonmesh:local-only@db:5432/carbonmesh?sslmode=disable"
    )

    assert configuration.url.drivername == "postgresql+asyncpg"
    assert configuration.url.host == "db"
    assert "sslmode" not in configuration.url.query
    assert "prepared_statement_cache_size" not in configuration.url.query
    assert "ssl" not in configuration.connect_args
    assert not configuration.is_neon
    assert not configuration.uses_tls


def test_normalize_url_rejects_markdown_wrapping_without_disclosing_secret() -> None:
    secret = (
        "postgresql://owner:[do-not-print@example.test]"
        "(mailto:do-not-print@example.test)/carbonmesh?sslmode=require"
    )

    with pytest.raises(DatabaseConfigurationError, match="formatted text") as caught:
        normalize_database_url(secret)

    assert "do-not-print" not in str(caught.value)


def test_database_target_exposes_identity_without_credentials(monkeypatch) -> None:
    secret = (
        "postgresql://owner:do-not-print@ep-example-pooler.aws.neon.tech/neondb"
        "?sslmode=require"
    )
    monkeypatch.setattr(database_session, "get_database_url", lambda: SecretStr(secret))

    target = get_database_target()

    assert target.endpoint_id == "ep-example"
    assert target.database == "neondb"
    assert "owner" not in repr(target)
    assert "do-not-print" not in repr(target)


@pytest.mark.asyncio
async def test_engine_is_lazy_and_uses_null_pool(monkeypatch) -> None:
    await database_session.dispose_engine()
    monkeypatch.setattr(
        database_session,
        "get_database_url",
        lambda: SecretStr(
            "postgresql://owner:do-not-print@ep-example-pooler.aws.neon.tech/neondb"
            "?sslmode=require"
        ),
    )

    engine = database_session.get_engine()

    assert isinstance(engine.sync_engine.pool, NullPool)
    await database_session.dispose_engine()


@pytest.mark.asyncio
async def test_session_scope_rolls_back_errors_and_always_closes(monkeypatch) -> None:
    fake_session = FakeAsyncSession()
    monkeypatch.setattr(
        database_session,
        "get_session_factory",
        lambda: lambda: fake_session,
    )

    with pytest.raises(ValueError, match="expected failure"):
        async with database_session.session_scope():
            raise ValueError("expected failure")

    assert fake_session.rolled_back
    assert fake_session.closed


class FakeAsyncSession:
    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    async def rollback(self) -> None:
        self.rolled_back = True

    async def close(self) -> None:
        self.closed = True
