import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import Lock

from pydantic import SecretStr
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_database_url


class DatabaseConfigurationError(RuntimeError):
    """A credential-safe database configuration failure."""


@dataclass(frozen=True, slots=True)
class AsyncDatabaseConfiguration:
    """Driver-ready URL and non-URL asyncpg options."""

    url: URL
    connect_args: dict[str, object]
    is_neon: bool
    uses_tls: bool


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    """Credential-free identity for confirming the selected database endpoint."""

    endpoint_id: str
    database: str


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_engine_lock = Lock()


_LOCAL_DATABASE_HOSTS = frozenset(
    {
        "127.0.0.1",
        "::1",
        "localhost",
        # Docker Compose service names used by the project and common local setups.
        "db",
        "postgres",
        "carbonmesh-db",
    }
)


def normalize_database_url(database_url: SecretStr | str) -> AsyncDatabaseConfiguration:
    """Build a secret-safe asyncpg configuration for local PostgreSQL or Neon.

    Remote databases must use authenticated TLS. Unencrypted connections are
    accepted only for explicit loopback/Docker service hosts so the local Docker
    stack can run without weakening the hosted-database policy.
    """
    raw_url = database_url.get_secret_value() if isinstance(database_url, SecretStr) else database_url
    if any(marker in raw_url for marker in ("mailto:", "[", "]", "\r", "\n")):
        raise DatabaseConfigurationError(
            "DATABASE_URL contains formatted text; provide the raw PostgreSQL URL."
        )
    try:
        url = make_url(raw_url)
    except (ArgumentError, TypeError, ValueError):
        raise DatabaseConfigurationError(
            "DATABASE_URL is not a valid PostgreSQL URL."
        ) from None

    if url.get_backend_name() != "postgresql":
        raise DatabaseConfigurationError("DATABASE_URL must use the PostgreSQL protocol.")
    if not url.host or not url.database:
        raise DatabaseConfigurationError("DATABASE_URL must include a host and database name.")

    host = (url.host or "").casefold()
    local_database = host in _LOCAL_DATABASE_HOSTS
    is_neon = host.endswith(".neon.tech")

    query = dict(url.query)
    ssl_mode_value = query.pop("sslmode", "disable" if local_database else "require")
    query.pop("channel_binding", None)
    if not isinstance(ssl_mode_value, str):
        raise DatabaseConfigurationError("DATABASE_URL must define one TLS mode.")
    ssl_mode = ssl_mode_value.lower()
    if ssl_mode not in {"disable", "require"}:
        raise DatabaseConfigurationError(
            "DATABASE_URL sslmode must be either require or disable for an approved local host."
        )
    if ssl_mode == "disable" and not local_database:
        raise DatabaseConfigurationError("Remote DATABASE_URL values must use sslmode=require.")

    # The SQLAlchemy asyncpg dialect owns its prepared statement cache. Disabling
    # it avoids name/cache conflicts on Neon's PgBouncer pooled endpoint.
    if is_neon:
        query["prepared_statement_cache_size"] = "0"
    async_url = url.set(drivername="postgresql+asyncpg", query=query)
    connect_args: dict[str, object] = {
        "timeout": 10,
        "command_timeout": 30,
    }
    if ssl_mode == "require":
        tls_context = ssl.create_default_context()
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        connect_args["ssl"] = tls_context
    if is_neon:
        connect_args["statement_cache_size"] = 0

    return AsyncDatabaseConfiguration(
        url=async_url,
        # An SSLContext validates both the certificate chain and hostname;
        # asyncpg's string "require" mode encrypts without authenticating.
        connect_args=connect_args,
        is_neon=is_neon,
        uses_tls=ssl_mode == "require",
    )


def get_database_target() -> DatabaseTarget:
    """Return only the endpoint identifier and database name from configuration."""
    database_url = get_database_url()
    if database_url is None:
        raise DatabaseConfigurationError(
            "DATABASE_URL is not configured. Copy .env.example to .env and add a PostgreSQL URL."
        )
    configuration = normalize_database_url(database_url)
    host_label = (configuration.url.host or "").split(".", 1)[0]
    endpoint_id = host_label.removesuffix("-pooler")
    database = configuration.url.database or ""
    if not endpoint_id or not database:  # pragma: no cover - normalized URL invariant
        raise DatabaseConfigurationError("DATABASE_URL has an incomplete database target.")
    return DatabaseTarget(endpoint_id=endpoint_id, database=database)


def get_engine() -> AsyncEngine:
    """Return one lazy async engine for local PostgreSQL or hosted Neon."""
    global _engine, _session_factory

    if _engine is not None:
        return _engine

    with _engine_lock:
        if _engine is not None:
            return _engine

        database_url = get_database_url()
        if database_url is None:
            raise DatabaseConfigurationError(
                "DATABASE_URL is not configured. Copy .env.example to .env and add a PostgreSQL URL."
            )

        configuration = normalize_database_url(database_url)
        engine_options: dict[str, object] = {
            "connect_args": configuration.connect_args,
            "echo": False,
            "pool_pre_ping": True,
        }
        # Neon pooled endpoints already provide connection pooling. Local and
        # ordinary PostgreSQL deployments use SQLAlchemy's bounded default pool.
        if configuration.is_neon:
            engine_options["poolclass"] = NullPool
        _engine = create_async_engine(configuration.url, **engine_options)
        _session_factory = async_sessionmaker(
            bind=_engine,
            autoflush=False,
            expire_on_commit=False,
        )
        return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory bound to the process-wide async engine."""
    if _session_factory is None:
        get_engine()
    if _session_factory is None:  # pragma: no cover - defensive invariant
        raise RuntimeError("The async session factory could not be initialized.")
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield a session, rolling back failures and never committing implicitly."""
    session = get_session_factory()()
    try:
        yield session
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    """Dispose connections and clear cached runtime objects during shutdown/tests."""
    global _engine, _session_factory

    with _engine_lock:
        engine = _engine
        _engine = None
        _session_factory = None
    if engine is not None:
        await engine.dispose()
