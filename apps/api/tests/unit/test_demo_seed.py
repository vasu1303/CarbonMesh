import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.demo import router
from app.db.models.carbon import ActivityRecord, EmissionFactor, RawActivityRecord
from app.db.models.core import EvidenceItem, SourceDocument
from app.db.models.procurement import SupplierProduct
from app.db.models.semantic import MetricDefinition
from app.modules.demo.fixtures import (
    DEMO_COMPANY_ID,
    DEMO_PERIOD_ID,
    DEMO_SITE_ID,
    build_demo_records,
)
from app.modules.demo.service import DemoResetSummary, _truncate_statement
from app.modules.imports.parser import parse_import_content


def test_demo_fixture_derives_documented_measurement_and_procurement_values() -> None:
    records = build_demo_records()
    activity = next(item for item in records if isinstance(item, ActivityRecord))
    factor = next(item for item in records if isinstance(item, EmissionFactor))
    products = {
        item.product_code: item for item in records if isinstance(item, SupplierProduct)
    }

    measured = activity.normalized_quantity * factor.factor_value
    projected = activity.normalized_quantity * products["TRAY-ALT-B"].pcf_kgco2e_per_unit
    avoided = measured - projected
    cost_delta = (
        (products["TRAY-ALT-B"].unit_cost - products["TRAY-CURRENT"].unit_cost)
        / products["TRAY-CURRENT"].unit_cost
        * Decimal(100)
    )

    assert measured == Decimal(33600)
    assert projected == Decimal(22800)
    assert avoided == Decimal(10800)
    assert cost_delta == Decimal("3.200")


def test_demo_fixture_contains_only_the_six_poc_metrics() -> None:
    metrics = {
        item.key for item in build_demo_records() if isinstance(item, MetricDefinition)
    }
    assert metrics == {
        "activity.purchased_material_mass",
        "emissions.scope3.category1",
        "supplier.product_carbon_footprint",
        "supplier.circularity_score",
        "procurement.projected_avoided_emissions",
        "procurement.cost_delta_pct",
    }


def test_demo_documents_and_evidence_match_checked_in_source_artifacts() -> None:
    records = build_demo_records()
    documents = {
        item.filename: item for item in records if isinstance(item, SourceDocument)
    }
    evidence = [item for item in records if isinstance(item, EvidenceItem)]
    fixture_directory = Path(__file__).resolve().parents[4] / "data" / "demo"

    for filename in ("activity.csv", "suppliers.json", "factor-evidence.txt"):
        content = (fixture_directory / filename).read_bytes()
        assert documents[filename].checksum == hashlib.sha256(content).hexdigest()
        assert documents[filename].size_bytes == len(content)

    factor_document = (fixture_directory / "factor-evidence.txt").read_bytes().decode("utf-8")
    factor_evidence = next(item for item in evidence if item.evidence_type == "emission_factor")
    assert factor_evidence.content_text == factor_document
    assert factor_evidence.checksum == hashlib.sha256(
        factor_document.encode("utf-8")
    ).hexdigest()

    supplier_document = (fixture_directory / "suppliers.json").read_bytes().decode("utf-8")
    supplier_evidence = [
        item for item in evidence if item.evidence_type == "supplier_product_declaration"
    ]
    assert len(supplier_evidence) == 3
    for item in supplier_evidence:
        assert item.content_text in supplier_document
        assert item.checksum == hashlib.sha256(item.content_text.encode("utf-8")).hexdigest()

    activity_text = (fixture_directory / "activity.csv").read_bytes().decode("utf-8")
    parsed_import = parse_import_content(activity_text, "text/csv")
    parsed_activity = parsed_import.records[0]
    raw_activity = next(item for item in records if isinstance(item, RawActivityRecord))
    expected_row_bytes = json.dumps(
        parsed_activity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert raw_activity.raw_payload == parsed_activity
    assert raw_activity.row_number == parsed_import.row_numbers[0] == 2
    assert raw_activity.checksum == hashlib.sha256(expected_row_bytes).hexdigest()


def test_demo_reset_truncates_only_the_authoritative_table_catalogue() -> None:
    statement = _truncate_statement()

    assert statement.startswith("TRUNCATE TABLE ")
    assert statement.endswith(" RESTART IDENTITY CASCADE")
    assert statement.count('"."') == 32
    assert '"core"."companies"' in statement
    assert '"ledger"."ledger_events"' in statement


def test_demo_reset_route_returns_stable_seed_context(monkeypatch) -> None:
    fake_session = object()

    @asynccontextmanager
    async def fake_session_scope() -> AsyncIterator[object]:
        yield fake_session

    async def fake_reset(session: object) -> DemoResetSummary:
        assert session is fake_session
        return DemoResetSummary(
            company_id=DEMO_COMPANY_ID,
            site_id=DEMO_SITE_ID,
            reporting_period_id=DEMO_PERIOD_ID,
            metric_count=6,
            activity_record_count=1,
            supplier_product_count=3,
            emission_factor_count=1,
        )

    monkeypatch.setattr("app.api.routes.demo.session_scope", fake_session_scope)
    monkeypatch.setattr("app.api.routes.demo.reset_and_seed_demo", fake_reset)
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/v1/demo")

    response = TestClient(test_app).post("/api/v1/demo/reset")

    assert response.status_code == 200
    assert response.json() == {
        "status": "reset",
        "synthetic": True,
        "company_id": str(DEMO_COMPANY_ID),
        "site_id": str(DEMO_SITE_ID),
        "reporting_period_id": str(DEMO_PERIOD_ID),
        "seeded": {
            "metrics": 6,
            "activity_records": 1,
            "supplier_products": 3,
            "emission_factors": 1,
        },
    }
