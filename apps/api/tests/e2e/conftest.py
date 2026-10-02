from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import ARRAY, Float, MetaData, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.types import TypeEngine

import app.db.models.ai
import app.db.models.assurance
import app.db.models.carbon
import app.db.models.core
import app.db.models.dispatch
import app.db.models.ledger
import app.db.models.procurement
import app.db.models.semantic
from app.db.base import Base
from app.db.ddl import APPLICATION_SCHEMAS, install_database_objects
from app.db.models.core import EvidenceItem
from app.db.session import normalize_database_url
from app.dependencies.database import get_db_session_factory
from app.main import app
from tests.e2e.seed import ApiE2ESeedIds, seed_api_e2e_data

EXTERNAL_E2E_URL_ENV = "CARBONMESH_E2E_DATABASE_URL"
EXTERNAL_E2E_OPT_IN_ENV = "CARBONMESH_RUN_API_E2E_TESTS"
EXTERNAL_E2E_OPT_IN_VALUE = "disposable-database"


@pytest.fixture(scope="session")
def expected_demo_results() -> dict[str, object]:
    """Load the committed synthetic expectations used by the HTTP journey."""

    fixture_path = Path(__file__).resolve().parents[4] / "data" / "demo" / "api_e2e_expected.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class DatabaseProcess:
    url: URL
    connect_args: dict[str, object]


@dataclass(frozen=True, slots=True)
class E2EContext:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    ids: ApiE2ESeedIds


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _run_postgres_command(arguments: list[str], *, purpose: str) -> None:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(
        arguments,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
        creationflags=creation_flags,
    )
    if completed.returncode != 0:
        pytest.fail(f"temporary PostgreSQL {purpose} failed; process output was redacted")


@pytest.fixture(scope="session")
def e2e_database() -> Iterator[DatabaseProcess]:
    """Provide an isolated database, never silently targeting configured app data."""

    external_url = os.getenv(EXTERNAL_E2E_URL_ENV)
    if external_url:
        if os.getenv(EXTERNAL_E2E_OPT_IN_ENV) != EXTERNAL_E2E_OPT_IN_VALUE:
            pytest.skip(
                f"{EXTERNAL_E2E_URL_ENV} requires explicit "
                f"{EXTERNAL_E2E_OPT_IN_ENV}={EXTERNAL_E2E_OPT_IN_VALUE}"
            )
        configuration = normalize_database_url(external_url)
        yield DatabaseProcess(configuration.url, configuration.connect_args)
        return

    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip(
            "API e2e tests require local PostgreSQL binaries or an explicitly opted-in "
            f"{EXTERNAL_E2E_URL_ENV}."
        )

    port = _available_port()
    with tempfile.TemporaryDirectory(prefix="carbonmesh-api-e2e-") as temporary_directory:
        data_directory = Path(temporary_directory) / "postgres"
        log_file = Path(temporary_directory) / "postgres.log"
        _run_postgres_command(
            [
                initdb,
                "-D",
                str(data_directory),
                "-A",
                "trust",
                "-U",
                "postgres",
                "--no-locale",
                "--encoding=UTF8",
            ],
            purpose="initialization",
        )
        _run_postgres_command(
            [
                pg_ctl,
                "-D",
                str(data_directory),
                "-l",
                str(log_file),
                "-o",
                f"-F -p {port} -h 127.0.0.1",
                "-w",
                "start",
            ],
            purpose="startup",
        )
        try:
            yield DatabaseProcess(
                URL.create(
                    "postgresql+asyncpg",
                    username="postgres",
                    host="127.0.0.1",
                    port=port,
                    database="postgres",
                ),
                {},
            )
        finally:
            _run_postgres_command(
                [pg_ctl, "-D", str(data_directory), "-m", "fast", "-w", "stop"],
                purpose="shutdown",
            )


def _portable_test_metadata() -> MetaData:
    """Clone production metadata, replacing only unused pgvector test storage."""

    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)

    evidence = metadata.tables["core.evidence_items"]
    evidence.c.embedding.type = ARRAY(Float)
    vector_index = next(
        index
        for index in evidence.indexes
        if index.name == "ix_core_evidence_items_embedding_cosine_hnsw"
    )
    evidence.indexes.remove(vector_index)
    return metadata


def _set_test_embedding_type(column_type: TypeEngine) -> None:
    """Keep ORM comparator caches aligned with the temporary test storage type."""
    attribute = EvidenceItem.embedding
    column = EvidenceItem.__table__.c.embedding
    column.type = column_type
    column._reset_memoizations()
    # Unit tests may have already memoized an annotated VECTOR column. Merely
    # replacing Table.c.embedding.type leaves its cosine operator cached on the
    # ORM comparator, producing an invalid ARRAY <=> parameter query in E2E.
    for owner, name in (
        (attribute.comparator, "__clause_element__"),
        (attribute.comparator, "expressions"),
        (attribute, "expression"),
    ):
        try:
            delattr(owner, name)
        except AttributeError:
            pass


@pytest_asyncio.fixture
async def e2e_context(e2e_database: DatabaseProcess) -> AsyncIterator[E2EContext]:
    """Create tables and arrange test-only prerequisites in a disposable database."""

    original_vector_type = Base.metadata.tables["core.evidence_items"].c.embedding.type
    _set_test_embedding_type(ARRAY(Float))
    engine = create_async_engine(
        e2e_database.url,
        connect_args=e2e_database.connect_args,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        metadata = _portable_test_metadata()
        async with engine.begin() as connection:
            # Every test receives a pristine copy of the eight-schema model. The
            # server itself is temporary (or explicitly opted-in as disposable),
            # so this cannot touch the configured CarbonMesh application database.
            for schema in reversed(APPLICATION_SCHEMAS):
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            for schema in APPLICATION_SCHEMAS:
                await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            await connection.run_sync(metadata.create_all)
            await install_database_objects(connection)

        async with session_factory() as session, session.begin():
            ids = await seed_api_e2e_data(session)

        yield E2EContext(engine=engine, session_factory=session_factory, ids=ids)
    finally:
        await engine.dispose()
        _set_test_embedding_type(original_vector_type)


@pytest_asyncio.fixture
async def api_client(e2e_context: E2EContext) -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides[get_db_session_factory] = lambda: e2e_context.session_factory
    transport = httpx.ASGITransport(app=app)
    try:
        # ASGITransport does not drive lifespan itself. Running it explicitly
        # exercises task draining and guarantees background runs finish before
        # the disposable schemas are torn down, including when a test fails.
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client,
        ):
            yield client
    finally:
        app.dependency_overrides.pop(get_db_session_factory, None)
