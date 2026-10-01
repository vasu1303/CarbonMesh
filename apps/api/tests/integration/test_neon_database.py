"""Opt-in contract tests for a rotated, disposable CarbonMesh Neon branch.

These tests intentionally do not reset or drop any database object.  They run only
when the operator explicitly attests that ``DATABASE_URL`` targets a disposable
Neon branch by setting::

    CARBONMESH_RUN_NEON_INTEGRATION_TESTS=rotated-disposable-branch

All ordinary test data is transaction-scoped.  The re-bootstrap preservation test
uses fixed, reserved identifiers so it can commit a row across processes, then
removes only those identifiers in a ``finally`` block.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Table, delete, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, DBAPIError, IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncConnection

import app.db.models  # noqa: F401  # Register the complete model catalogue.
from app.core.config import get_database_url
from app.db.base import Base
from app.db.bootstrap import load_model_registry, verify_database_contract
from app.db.session import get_engine

OPT_IN_ENV = "CARBONMESH_RUN_NEON_INTEGRATION_TESTS"
OPT_IN_VALUE = "rotated-disposable-branch"
API_ROOT = Path(__file__).resolve().parents[2]


def _configured_database_url() -> str | None:
    secret = get_database_url()
    return None if secret is None else secret.get_secret_value()


def _is_neon_url(database_url: str | None) -> bool:
    if not database_url:
        return False
    try:
        host = make_url(database_url).host or ""
    except (ArgumentError, TypeError, ValueError):
        return False
    return host == "neon.tech" or host.endswith(".neon.tech")


TARGET_IS_NEON = _is_neon_url(_configured_database_url())
LIVE_TESTS_ENABLED = (
    os.getenv(OPT_IN_ENV) == OPT_IN_VALUE and TARGET_IS_NEON
)

pytestmark = pytest.mark.skipif(
    not LIVE_TESTS_ENABLED,
    reason=(
        "requires a Neon DATABASE_URL for a rotated disposable branch and explicit "
        f"{OPT_IN_ENV}={OPT_IN_VALUE} opt-in"
    ),
)


def _run_bootstrap(*arguments: str) -> None:
    """Run the public bootstrap command without ever including captured output."""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "app.db.bootstrap", *arguments],
            cwd=API_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("database bootstrap timed out; subprocess output was redacted")

    assert completed.returncode == 0, (
        "database bootstrap failed; subprocess output was intentionally redacted"
    )


@pytest.fixture(scope="module", autouse=True)
def bootstrapped_live_database() -> None:
    """Exercise a fresh/idempotent bootstrap twice, then the read-only check CLI."""
    _run_bootstrap()
    _run_bootstrap()
    _run_bootstrap("--check")


@pytest_asyncio.fixture
async def connection() -> AsyncIterator[AsyncConnection]:
    """Provide a live connection whose test writes are always rolled back."""
    async with get_engine().connect() as live_connection:
        transaction = await live_connection.begin()
        try:
            yield live_connection
        finally:
            if transaction.is_active:
                await transaction.rollback()


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    company_id: UUID
    data_source_id: UUID
    source_document_id: UUID


@dataclass(frozen=True, slots=True)
class ProcurementContext:
    company_id: UUID
    actor_id: UUID
    scenario_id: UUID
    recommendation_id: UUID


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _vector_literal(*, axis: int, dimensions: int = 768) -> str:
    values = ["0"] * dimensions
    if 0 <= axis < dimensions:
        values[axis] = "1"
    return f"[{','.join(values)}]"


async def _insert_row(
    connection: AsyncConnection,
    table_name: str,
    **values: Any,
) -> UUID:
    table = _table(table_name)
    result = await connection.execute(
        insert(table).values(**values).returning(table.c.id)
    )
    return result.scalar_one()


async def _create_company(connection: AsyncConnection, token: str) -> UUID:
    return await _insert_row(
        connection,
        "core.companies",
        code=f"it-{token}",
        name=f"Neon integration {token}",
        is_synthetic=True,
    )


async def _create_evidence_context(
    connection: AsyncConnection,
    token: str,
    *,
    company_id: UUID | None = None,
) -> EvidenceContext:
    resolved_company_id = company_id or await _create_company(connection, token)
    source_id = await _insert_row(
        connection,
        "core.data_sources",
        company_id=resolved_company_id,
        name=f"integration-source-{token}",
        source_type="synthetic",
        status="ready",
        configuration={"test": True},
        is_synthetic=True,
    )
    document_id = await _insert_row(
        connection,
        "core.source_documents",
        company_id=resolved_company_id,
        data_source_id=source_id,
        filename=f"integration-{token}.txt",
        content_type="text/plain",
        checksum=_sha256(f"document-{token}"),
        size_bytes=128,
        document_metadata={"test": True},
    )
    return EvidenceContext(resolved_company_id, source_id, document_id)


async def _insert_evidence(
    connection: AsyncConnection,
    context: EvidenceContext,
    *,
    token: str,
    evidence_type: str,
    vector: str,
) -> UUID:
    result = await connection.execute(
        text(
            """
            INSERT INTO core.evidence_items (
                company_id,
                source_document_id,
                evidence_type,
                locator,
                content_text,
                checksum,
                metadata,
                embedding,
                embedding_model,
                embedded_at
            )
            VALUES (
                :company_id,
                :document_id,
                :evidence_type,
                :locator,
                :content_text,
                :checksum,
                CAST(:metadata AS jsonb),
                CAST(:embedding AS vector),
                :embedding_model,
                :embedded_at
            )
            RETURNING id
            """
        ),
        {
            "company_id": context.company_id,
            "document_id": context.source_document_id,
            "evidence_type": evidence_type,
            "locator": f"integration:{token}",
            "content_text": f"integration evidence {token}",
            "checksum": _sha256(f"evidence-{token}"),
            "metadata": '{"integration_test": true}',
            "embedding": vector,
            "embedding_model": "gemini-embedding-2-integration-test",
            "embedded_at": datetime.now(UTC),
        },
    )
    return result.scalar_one()


def _sqlstate(error: BaseException) -> str | None:
    """Find a PostgreSQL SQLSTATE through SQLAlchemy/asyncpg wrappers."""
    pending: list[BaseException] = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        state = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if isinstance(state, str):
            return state
        for attribute in ("orig", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                pending.append(nested)
    return None


def _constraint_name(error: BaseException) -> str | None:
    pending: list[BaseException] = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        name = getattr(current, "constraint_name", None)
        if isinstance(name, str):
            return name
        for attribute in ("orig", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                pending.append(nested)
    return None


async def _table_oids(connection: AsyncConnection) -> dict[str, int]:
    result = await connection.execute(
        text(
            """
            SELECT ns.nspname || '.' || rel.relname AS qualified_name, rel.oid
            FROM pg_class AS rel
            JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
            WHERE rel.relkind IN ('r', 'p')
              AND ns.nspname IN
                  ('core', 'semantic', 'ai', 'carbon', 'ledger', 'assurance',
                   'procurement', 'dispatch')
            ORDER BY qualified_name
            """
        )
    )
    return {name: oid for name, oid in result.tuples()}


@pytest.mark.asyncio
async def test_live_contract_is_complete(connection: AsyncConnection) -> None:
    metadata = load_model_registry()

    await verify_database_contract(connection, metadata)

    assert len(metadata.tables) == 46
    assert len(await _table_oids(connection)) == 46


PRESERVATION_COMPANY_ID = UUID("f976c3a6-9706-5abc-9537-f9bb2a167421")
PRESERVATION_SOURCE_ID = UUID("12ec22c6-119e-56f8-ab04-7961e357b8d5")
PRESERVATION_DOCUMENT_ID = UUID("d9074294-eae1-5b46-907a-68a2cd8e5d27")
PRESERVATION_EVIDENCE_ID = UUID("1b3cde20-474c-59a6-b346-b07dc13bfb4a")
PRESERVATION_COMPANY_CODE = "__carbonmesh_neon_integration_preservation_initial__"


async def _remove_preservation_rows(connection: AsyncConnection) -> None:
    company = _table("core.companies")
    existing_code = await connection.scalar(
        select(company.c.code).where(company.c.id == PRESERVATION_COMPANY_ID)
    )
    if existing_code is not None and existing_code != PRESERVATION_COMPANY_CODE:
        pytest.fail("reserved integration-test UUID is already owned by non-test data")

    await connection.execute(
        delete(_table("core.evidence_items")).where(
            _table("core.evidence_items").c.id == PRESERVATION_EVIDENCE_ID
        )
    )
    await connection.execute(
        delete(_table("core.source_documents")).where(
            _table("core.source_documents").c.id == PRESERVATION_DOCUMENT_ID
        )
    )
    await connection.execute(
        delete(_table("core.data_sources")).where(
            _table("core.data_sources").c.id == PRESERVATION_SOURCE_ID
        )
    )
    await connection.execute(
        delete(company).where(company.c.id == PRESERVATION_COMPANY_ID)
    )


async def _install_preservation_row(connection: AsyncConnection) -> None:
    await _remove_preservation_rows(connection)
    await connection.execute(
        insert(_table("core.companies")).values(
            id=PRESERVATION_COMPANY_ID,
            code=PRESERVATION_COMPANY_CODE,
            name="CarbonMesh Neon integration preservation row",
            is_synthetic=True,
        )
    )
    await connection.execute(
        insert(_table("core.data_sources")).values(
            id=PRESERVATION_SOURCE_ID,
            company_id=PRESERVATION_COMPANY_ID,
            name="__carbonmesh_neon_integration_preservation_source__",
            source_type="synthetic",
            status="ready",
            configuration={"integration_test": True},
            is_synthetic=True,
        )
    )
    await connection.execute(
        insert(_table("core.source_documents")).values(
            id=PRESERVATION_DOCUMENT_ID,
            company_id=PRESERVATION_COMPANY_ID,
            data_source_id=PRESERVATION_SOURCE_ID,
            filename="preservation.txt",
            content_type="text/plain",
            checksum=_sha256("neon-bootstrap-preservation-document-initial"),
            size_bytes=1,
            document_metadata={"integration_test": True},
        )
    )
    await connection.execute(
        text(
            """
            INSERT INTO core.evidence_items (
                id,
                company_id,
                source_document_id,
                evidence_type,
                locator,
                content_text,
                checksum,
                metadata,
                embedding,
                embedding_model,
                embedded_at
            )
            VALUES (
                :id,
                :company_id,
                :document_id,
                'integration_test',
                'integration:bootstrap-preservation-initial',
                'bootstrap preservation vector',
                :checksum,
                '{"integration_test": true}'::jsonb,
                CAST(:embedding AS vector),
                'gemini-embedding-2-integration-test',
                :embedded_at
            )
            """
        ),
        {
            "id": PRESERVATION_EVIDENCE_ID,
            "company_id": PRESERVATION_COMPANY_ID,
            "document_id": PRESERVATION_DOCUMENT_ID,
            "checksum": _sha256("neon-bootstrap-preservation-evidence-initial"),
            "embedding": _vector_literal(axis=0),
            "embedded_at": datetime.now(UTC),
        },
    )


@pytest.mark.asyncio
async def test_rebootstrap_preserves_vector_row_and_table_identities() -> None:
    engine = get_engine()
    try:
        async with engine.begin() as write_connection:
            await _install_preservation_row(write_connection)
            original_oids = await _table_oids(write_connection)

        await asyncio.to_thread(_run_bootstrap)

        async with engine.connect() as verify_connection:
            current_oids = await _table_oids(verify_connection)
            distance = await verify_connection.scalar(
                text(
                    """
                    SELECT embedding <=> CAST(:embedding AS vector)
                    FROM core.evidence_items
                    WHERE id = :evidence_id
                      AND company_id = :company_id
                    """
                ),
                {
                    "embedding": _vector_literal(axis=0),
                    "evidence_id": PRESERVATION_EVIDENCE_ID,
                    "company_id": PRESERVATION_COMPANY_ID,
                },
            )

        assert current_oids == original_oids
        assert distance == pytest.approx(0.0)
    finally:
        async with engine.begin() as cleanup_connection:
            await _remove_preservation_rows(cleanup_connection)


@pytest.mark.asyncio
async def test_vector_dimension_and_filtered_cosine_retrieval(
    connection: AsyncConnection,
) -> None:
    token = uuid4().hex
    primary = await _create_evidence_context(connection, f"primary-{token}")
    other_tenant = await _create_evidence_context(connection, f"other-{token}")

    nearest_id = await _insert_evidence(
        connection,
        primary,
        token=f"nearest-{token}",
        evidence_type="supplier_document",
        vector=_vector_literal(axis=0),
    )
    farther_id = await _insert_evidence(
        connection,
        primary,
        token=f"farther-{token}",
        evidence_type="supplier_document",
        vector=_vector_literal(axis=1),
    )
    await _insert_evidence(
        connection,
        primary,
        token=f"wrong-type-{token}",
        evidence_type="excluded_type",
        vector=_vector_literal(axis=0),
    )
    await _insert_evidence(
        connection,
        other_tenant,
        token=f"other-tenant-{token}",
        evidence_type="supplier_document",
        vector=_vector_literal(axis=0),
    )

    with pytest.raises(StatementError):
        async with connection.begin_nested():
            await _insert_evidence(
                connection,
                primary,
                token=f"wrong-dimension-{token}",
                evidence_type="supplier_document",
                vector=_vector_literal(axis=0, dimensions=767),
            )

    result = await connection.execute(
        text(
            """
            SELECT id, embedding <=> CAST(:query_embedding AS vector) AS distance
            FROM core.evidence_items
            WHERE company_id = :company_id
              AND source_document_id = :document_id
              AND evidence_type = :evidence_type
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:query_embedding AS vector), id
            LIMIT 2
            """
        ),
        {
            "query_embedding": _vector_literal(axis=0),
            "company_id": primary.company_id,
            "document_id": primary.source_document_id,
            "evidence_type": "supplier_document",
        },
    )
    rows = result.tuples().all()

    assert [row.id for row in rows] == [nearest_id, farther_id]
    assert rows[0].distance == pytest.approx(0.0)
    assert rows[1].distance == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_representative_tenant_numeric_and_uniqueness_constraints(
    connection: AsyncConnection,
) -> None:
    token = uuid4().hex
    first = await _create_evidence_context(connection, f"first-{token}")
    second_company_id = await _create_company(connection, f"second-{token}")
    site_id = await _insert_row(
        connection,
        "core.sites",
        company_id=first.company_id,
        code=f"site-{token}",
        name="Integration site",
        country_code="IN",
        timezone="Asia/Kolkata",
    )

    with pytest.raises(IntegrityError) as cross_tenant_error:
        async with connection.begin_nested():
            await connection.execute(
                insert(_table("core.data_sources")).values(
                    company_id=second_company_id,
                    site_id=site_id,
                    name=f"cross-tenant-{token}",
                    source_type="synthetic",
                    status="ready",
                )
            )
    assert _sqlstate(cross_tenant_error.value) == "23503"

    with pytest.raises(IntegrityError) as numeric_error:
        async with connection.begin_nested():
            await connection.execute(
                insert(_table("carbon.raw_activity_records")).values(
                    company_id=first.company_id,
                    data_source_id=first.data_source_id,
                    source_document_id=first.source_document_id,
                    row_key=f"negative-row-{token}",
                    row_number=0,
                    raw_payload={"test": True},
                    checksum=_sha256(f"negative-row-{token}"),
                    import_status="accepted",
                )
            )
    assert _sqlstate(numeric_error.value) == "23514"

    original_checksum = _sha256(f"document-first-{token}")
    with pytest.raises(IntegrityError) as uniqueness_error:
        async with connection.begin_nested():
            await connection.execute(
                insert(_table("core.source_documents")).values(
                    company_id=first.company_id,
                    data_source_id=first.data_source_id,
                    filename="duplicate-checksum.txt",
                    content_type="text/plain",
                    checksum=original_checksum,
                    size_bytes=1,
                )
            )
    assert _sqlstate(uniqueness_error.value) == "23505"
    assert _constraint_name(uniqueness_error.value) == "uq_core_documents_company_checksum"


async def _create_procurement_context(
    connection: AsyncConnection,
    token: str,
) -> ProcurementContext:
    company_id = await _create_company(connection, f"proc-{token}")
    actor_id = await _insert_row(
        connection,
        "core.actors",
        company_id=company_id,
        email=f"integration-{token}@example.invalid",
        display_name="Integration approver",
        role="approver",
    )
    site_id = await _insert_row(
        connection,
        "core.sites",
        company_id=company_id,
        code=f"proc-site-{token}",
        name="Procurement integration site",
        country_code="IN",
    )
    period_id = await _insert_row(
        connection,
        "core.reporting_periods",
        company_id=company_id,
        name=f"Integration period {token}",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 9, 30),
        status="closed",
    )
    metric_id = await _insert_row(
        connection,
        "semantic.metric_definitions",
        company_id=company_id,
        key=f"integration.metric.{token}",
        version="1.0.0",
        name="Integration metric",
        canonical_unit="kgCO2e",
        dimensions={"site": True},
        handler="integration_test",
        method_version="1.0.0",
    )
    method_id = await _insert_row(
        connection,
        "semantic.method_definitions",
        company_id=company_id,
        method_type="supplier_scoring",
        key=f"integration.scoring.{token}",
        version="1.0.0",
        name="Integration scoring",
        code_version="integration-test",
        configuration={
            "carbon": "0.40",
            "evidence": "0.25",
            "circularity": "0.20",
            "operational_fit": "0.15",
        },
        effective_from=date(2026, 1, 1),
    )
    supplier_id = await _insert_row(
        connection,
        "procurement.suppliers",
        company_id=company_id,
        supplier_code=f"supplier-{token}",
        name="Integration supplier",
        country_code="IN",
        status="active",
    )

    product_common = {
        "company_id": company_id,
        "supplier_id": supplier_id,
        "material_code": "integration-material",
        "category": "integration-category",
        "pcf_unit": "kgCO2e/kg",
        "circularity_score": Decimal(70),
        "recycled_content_pct": Decimal(50),
        "recyclable_pct": Decimal(90),
        "evidence_quality_score": Decimal(85),
        "lead_time_days": 10,
        "currency": "USD",
        "effective_from": date(2026, 1, 1),
        "is_active": True,
    }
    baseline_product_id = await _insert_row(
        connection,
        "procurement.supplier_products",
        **product_common,
        product_code=f"baseline-{token}",
        name="Baseline product",
        pcf_kgco2e_per_unit=Decimal("2.8"),
        unit_cost=Decimal("1.00"),
    )
    recommended_product_id = await _insert_row(
        connection,
        "procurement.supplier_products",
        **product_common,
        product_code=f"recommended-{token}",
        name="Recommended product",
        pcf_kgco2e_per_unit=Decimal("1.9"),
        unit_cost=Decimal("1.032"),
    )
    calculation_run_id = await _insert_row(
        connection,
        "carbon.calculation_runs",
        company_id=company_id,
        reporting_period_id=period_id,
        method_definition_id=method_id,
        method_version="1.0.0",
        code_version="integration-test",
        rounding_policy="ROUND_HALF_EVEN",
        input_hash=_sha256(f"calculation-input-{token}"),
        output_hash=_sha256(f"calculation-output-{token}"),
        status="completed",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        summary={"integration_test": True},
    )
    measurement_id = await _insert_row(
        connection,
        "carbon.carbon_measurements",
        company_id=company_id,
        calculation_run_id=calculation_run_id,
        site_id=site_id,
        reporting_period_id=period_id,
        metric_definition_id=metric_id,
        value_kgco2e=Decimal(33600),
        unit="kgCO2e",
        confidence=Decimal("0.95"),
        status="verified",
        formula="12000 * 2.8",
        output_hash=_sha256(f"measurement-output-{token}"),
        verified_at=datetime.now(UTC),
    )
    signature = _sha256(f"scenario-{token}")
    scenario_id = await _insert_row(
        connection,
        "procurement.procurement_scenarios",
        company_id=company_id,
        site_id=site_id,
        reporting_period_id=period_id,
        current_product_id=baseline_product_id,
        carbon_measurement_id=measurement_id,
        method_definition_id=method_id,
        quantity=Decimal(12000),
        quantity_unit="kg",
        current_unit_cost=Decimal("1.00"),
        currency="USD",
        max_cost_increase_pct=Decimal(5),
        max_lead_time_days=20,
        minimum_circularity_score=Decimal(50),
        material_constraints={"material_code": "integration-material"},
        analysis_signature=signature,
        frozen_context={"integration_test": True},
        status="assessed",
    )
    score_id = await _insert_row(
        connection,
        "procurement.supplier_scores",
        company_id=company_id,
        scenario_id=scenario_id,
        supplier_product_id=recommended_product_id,
        method_definition_id=method_id,
        carbon_score=Decimal(95),
        evidence_score=Decimal(85),
        circularity_score=Decimal(70),
        operational_fit_score=Decimal(80),
        total_score=Decimal("85.25"),
        rank=1,
        feasible=True,
        infeasibility_reasons=[],
    )
    recommendation_id = await _insert_row(
        connection,
        "procurement.procurement_recommendations",
        company_id=company_id,
        scenario_id=scenario_id,
        recommended_product_id=recommended_product_id,
        baseline_product_id=baseline_product_id,
        supplier_score_id=score_id,
        status="pending_approval",
        projected_footprint_kgco2e=Decimal(22800),
        avoided_kgco2e=Decimal(10800),
        reduction_pct=Decimal("32.1429"),
        cost_delta_pct=Decimal("3.2"),
        lead_time_delta_days=0,
        rationale_template="Integration recommendation",
        analysis_signature=signature,
        payload_hash=_sha256(f"recommendation-payload-{token}"),
        impact_snapshot={"integration_test": True},
    )
    return ProcurementContext(company_id, actor_id, scenario_id, recommendation_id)


@pytest.mark.asyncio
async def test_active_recommendation_and_pending_approval_are_unique(
    connection: AsyncConnection,
) -> None:
    token = uuid4().hex
    context = await _create_procurement_context(connection, token)
    recommendation = _table("procurement.procurement_recommendations")
    existing = (
        await connection.execute(
            select(recommendation).where(recommendation.c.id == context.recommendation_id)
        )
    ).mappings().one()

    duplicate_values = {
        key: value
        for key, value in existing.items()
        if key not in {"id", "created_at", "payload_hash"}
    }
    duplicate_values["payload_hash"] = _sha256(f"second-recommendation-{token}")
    with pytest.raises(IntegrityError) as recommendation_error:
        async with connection.begin_nested():
            await connection.execute(insert(recommendation).values(**duplicate_values))
    assert _sqlstate(recommendation_error.value) == "23505"
    assert (
        _constraint_name(recommendation_error.value)
        == "uq_proc_recommendations_active_scenario"
    )

    approval_values = {
        "company_id": context.company_id,
        "recommendation_id": context.recommendation_id,
        "requested_by": context.actor_id,
        "status": "pending",
        "preview_hash": _sha256(f"mismatched-approval-preview-{token}"),
        "analysis_signature": existing["analysis_signature"],
        "idempotency_key": f"approval-{token}-one",
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }

    with pytest.raises(IntegrityError) as hash_binding_error:
        async with connection.begin_nested():
            await connection.execute(
                insert(_table("core.approvals")).values(**approval_values)
            )
    assert _sqlstate(hash_binding_error.value) == "23503"
    assert (
        _constraint_name(hash_binding_error.value)
        == "fk_core_approvals_recommendation_preview"
    )

    approval_values["preview_hash"] = existing["payload_hash"]
    await connection.execute(insert(_table("core.approvals")).values(**approval_values))

    approval_values["idempotency_key"] = f"approval-{token}-two"
    with pytest.raises(IntegrityError) as approval_error:
        async with connection.begin_nested():
            await connection.execute(
                insert(_table("core.approvals")).values(**approval_values)
            )
    assert _sqlstate(approval_error.value) == "23505"
    assert (
        _constraint_name(approval_error.value)
        == "uq_core_approvals_pending_recommendation"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_ledger_events_are_immutable(
    connection: AsyncConnection,
    operation: str,
) -> None:
    token = uuid4().hex
    company_id = await _create_company(connection, f"ledger-{operation}-{token}")
    event_id = await _insert_row(
        connection,
        "ledger.ledger_events",
        company_id=company_id,
        event_type="integration.test",
        entity_type="integration_fixture",
        entity_id=uuid4(),
        payload={"operation": operation},
        payload_hash=_sha256(f"ledger-{operation}-{token}"),
    )
    ledger_event = _table("ledger.ledger_events")
    statement = (
        update(ledger_event)
        .where(ledger_event.c.id == event_id)
        .values(event_type="integration.mutated")
        if operation == "update"
        else delete(ledger_event).where(ledger_event.c.id == event_id)
    )

    with pytest.raises(DBAPIError) as mutation_error:
        async with connection.begin_nested():
            await connection.execute(statement)

    assert _sqlstate(mutation_error.value) == "55000"
    assert await connection.scalar(
        select(ledger_event.c.id).where(ledger_event.c.id == event_id)
    ) == event_id


def test_opt_in_contract_is_explicit_and_secret_safe() -> None:
    """Keep the dangerous live-test switch difficult to enable accidentally."""
    assert OPT_IN_VALUE == "rotated-disposable-branch"
    assert TARGET_IS_NEON
