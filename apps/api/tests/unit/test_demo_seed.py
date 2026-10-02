import csv
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.demo import authorize_demo_reset, router
from app.db.models.carbon import ActivityRecord, EmissionFactor, RawActivityRecord
from app.db.models.core import DataSource, EvidenceItem, SourceDocument
from app.db.models.dispatch import FlexibleLoad, OperatingConstraint
from app.db.models.procurement import SupplierProduct
from app.db.models.semantic import MetricDefinition
from app.dependencies.database import get_db_session
from app.modules.demo.fixtures import (
    ALTERNATIVE_A_PRODUCT_ID,
    ALTERNATIVE_B_PRODUCT_ID,
    ALTERNATIVE_C_PRODUCT_ID,
    CURRENT_PRODUCT_ID,
    DEMO_COMPANY_ID,
    DEMO_PERIOD_ID,
    DEMO_SITE_ID,
    RUNTIME_FIXTURE_NAMES,
    SCORING_METHOD_ID,
    _load_fixture_bundle,
    build_demo_records,
)
from app.modules.demo.service import DemoResetSummary, _truncate_statement
from app.modules.imports.parser import parse_import_content
from app.modules.ledger.service import payload_sha256
from app.modules.measurement.domain import resolve_factor
from app.modules.measurement.service import MeasurementService
from app.modules.procurement.review import scenario_context_signature
from app.modules.procurement.schemas import ScoringMethod, SupplierProductSummary


def test_demo_fixture_derives_documented_measurement_and_procurement_values() -> None:
    records = build_demo_records()
    activity = next(item for item in records if isinstance(item, ActivityRecord))
    factor = next(item for item in records if isinstance(item, EmissionFactor))
    products = {item.product_code: item for item in records if isinstance(item, SupplierProduct)}

    measured = activity.normalized_quantity * factor.factor_value
    projected = activity.normalized_quantity * products["AL-RECYCLED-B"].pcf_kgco2e_per_unit
    avoided = measured - projected
    cost_delta = (
        (products["AL-RECYCLED-B"].unit_cost - products["AL-CURRENT"].unit_cost)
        / products["AL-CURRENT"].unit_cost
        * Decimal(100)
    )

    assert measured == Decimal(86000)
    assert projected == Decimal(24000)
    assert avoided == Decimal(62000)
    assert cost_delta == Decimal("4.00")

    supplier_source = next(
        item
        for item in records
        if isinstance(item, DataSource) and item.configuration.get("import_type") == "suppliers"
    )
    assert supplier_source.configuration["accepted_count"] == len(products) == 4

    supplier_fixture = json.loads(
        (Path(__file__).resolve().parents[4] / "data" / "demo" / "suppliers.json").read_text(
            encoding="utf-8"
        )
    )
    expected_product_ids = {
        "AL-CURRENT": CURRENT_PRODUCT_ID,
        "AL-RECYCLED-A": ALTERNATIVE_A_PRODUCT_ID,
        "AL-RECYCLED-B": ALTERNATIVE_B_PRODUCT_ID,
        "AL-RECYCLED-C": ALTERNATIVE_C_PRODUCT_ID,
    }
    assert {item["product_code"] for item in supplier_fixture["products"]} == set(
        expected_product_ids
    )
    for source in supplier_fixture["products"]:
        product = products[source["product_code"]]
        assert product.id == expected_product_ids[source["product_code"]]
        assert product.name == source["name"]
        assert product.description == source["description"]
        assert product.material_code == source["material_code"]
        assert product.category == source["category"]
        assert product.pcf_kgco2e_per_unit == Decimal(source["pcf_kgco2e_per_unit"])
        assert product.circularity_score == Decimal(source["circularity_score"])
        assert product.evidence_quality_score == Decimal(source["evidence_quality_score"])
        assert product.unit_cost == Decimal(source["unit_cost"])


def test_demo_fixture_contains_the_four_module_poc_metrics() -> None:
    metrics = {item.key for item in build_demo_records() if isinstance(item, MetricDefinition)}
    assert metrics == {
        "activity.purchased_material_mass",
        "emissions.scope3.category1",
        "supplier.product_carbon_footprint",
        "supplier.circularity_score",
        "procurement.projected_avoided_emissions",
        "procurement.cost_delta_pct",
        "activity.electricity_consumption",
        "emissions.scope2.location_based",
        "electricity.grid_carbon_intensity",
    }


def test_demo_documents_and_evidence_match_checked_in_source_artifacts() -> None:
    records = build_demo_records()
    documents = {item.filename: item for item in records if isinstance(item, SourceDocument)}
    evidence = [item for item in records if isinstance(item, EvidenceItem)]
    fixture_directory = Path(__file__).resolve().parents[4] / "data" / "demo"

    for filename in (
        "activity.csv",
        "suppliers.json",
        "factor-evidence.txt",
        "electricity-hourly.csv",
        "assurance-standard.json",
        "assurance-evidence.md",
    ):
        content = (fixture_directory / filename).read_bytes()
        assert documents[filename].checksum == hashlib.sha256(content).hexdigest()
        assert documents[filename].size_bytes == len(content)

    factor_document = (fixture_directory / "factor-evidence.txt").read_bytes().decode("utf-8")
    assert factor_document.startswith("SYNTHETIC DEMO EVIDENCE - ")
    assert "\ufffd" not in factor_document
    factor_evidence = next(item for item in evidence if item.evidence_type == "emission_factor")
    assert factor_evidence.content_text == factor_document
    assert factor_evidence.checksum == hashlib.sha256(factor_document.encode("utf-8")).hexdigest()

    supplier_document = (fixture_directory / "suppliers.json").read_bytes().decode("utf-8")
    supplier_evidence = [
        item for item in evidence if item.evidence_type == "supplier_product_declaration"
    ]
    assert len(supplier_evidence) == 4
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

    assurance_document = (fixture_directory / "assurance-evidence.md").read_text(encoding="utf-8")
    assert assurance_document.startswith("# Maverick Manufacturing - synthetic evidence")
    assert "\ufffd" not in assurance_document
    assurance_evidence = next(
        item for item in evidence if item.evidence_type == "disclosure_support"
    )
    assert assurance_evidence.content_text == assurance_document
    assert assurance_evidence.embedding_model == "carbonmesh-hash-768-v1"
    assert assurance_evidence.embedded_at is not None
    assert len(assurance_evidence.embedding) == 768
    assert assurance_evidence.evidence_metadata["requirement_codes"] == [
        "S2-BOUNDARY",
        "S2-TOTAL",
    ]


def test_demo_fixture_seeds_advisory_batch_process_and_hard_constraints() -> None:
    records = build_demo_records()
    load = next(item for item in records if isinstance(item, FlexibleLoad))
    constraints = [item for item in records if isinstance(item, OperatingConstraint)]

    assert load.code == "BATCH-PROCESS-7"
    assert load.power_kw == Decimal(500)
    assert load.duration_minutes == 120
    assert load.energy_kwh == Decimal(1000)
    assert load.load_metadata["actuation_authorized"] is False
    assert {item.constraint_type for item in constraints} == {
        "availability",
        "deadline",
        "blackout",
        "power",
    }
    assert all(item.is_hard for item in constraints)
    assert next(
        item for item in constraints if item.constraint_type == "deadline"
    ).configuration == {
        "baseline_start": "2026-10-01T08:00:00+00:00",
        "maximum_delay_minutes": 240,
    }


def test_versioned_manifest_covers_every_golden_input_and_matches_hashes() -> None:
    fixture_directory = Path(__file__).resolve().parents[4] / "data" / "demo"
    manifest = json.loads((fixture_directory / "manifest-v1.json").read_text(encoding="utf-8"))

    assert manifest["fixture_id"] == "maverick-q3-2026-v1"
    assert manifest["synthetic"] is True
    for artifact in manifest["artifacts"]:
        relative = Path(artifact["path"])
        content = (fixture_directory.parent.parent / relative).read_bytes()
        assert hashlib.sha256(content).hexdigest() == artifact["sha256"]


def test_complete_quarter_is_contiguous_and_its_decimal_golden_matches_sources() -> None:
    directory = Path(__file__).resolve().parents[4] / "data" / "demo"
    manifest = json.loads((directory / "complete-q3-manifest-v1.json").read_text())
    assert manifest["fixture_id"] == "maverick-q3-2026-complete-v1"
    assert manifest["synthetic"] is True
    for artifact in manifest["artifacts"]:
        assert hashlib.sha256((directory / Path(artifact["path"]).name).read_bytes()).hexdigest() == artifact["sha256"]
    with (directory / "electricity-hourly-complete-q3-v1.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    history = json.loads((directory / "grid-history-complete-q3-v1.json").read_text())
    quality_history = json.loads((directory / "grid-history.json").read_text())
    complete_points = {point["datetime"]: point for point in history["data"]}
    assert all(complete_points[point["datetime"]] == point for point in quality_history["data"])
    assert len(rows) == len(history["data"]) == manifest["hourly_intervals"] == 92 * 24
    expected_times = [datetime(2026, 7, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(2208)]
    assert [datetime.fromisoformat(row["timestamp"]) for row in rows] == expected_times
    assert [datetime.fromisoformat(point["datetime"]) for point in history["data"]] == expected_times
    assert all(row["synthetic"] == "true" for row in rows)
    total = sum(
        Decimal(row["kwh"]) * Decimal(point["carbonIntensity"]) / Decimal(1000)
        for row, point in zip(rows, history["data"], strict=True)
    )
    assert total == Decimal(manifest["expected"]["scope2_kgco2e"])
    assert sum(Decimal(row["kwh"]) for row in rows) == Decimal(manifest["expected"]["electricity_kwh"])


def test_declared_products_have_specific_factors_and_trusted_source_metadata() -> None:
    records = build_demo_records()
    products = [item for item in records if isinstance(item, SupplierProduct)]
    factors = [MeasurementService._factor_candidate(item) for item in records if isinstance(item, EmissionFactor)]
    for product in products:
        factor = resolve_factor(
            factors,
            material_code=product.material_code,
            product_code=product.product_code,
            geography="IN",
            effective_on=date(2026, 9, 15),
        )
        assert factor.candidate.factor_value == product.pcf_kgco2e_per_unit
    documents = {item.id: item for item in records if isinstance(item, SourceDocument)}
    for item in records:
        if isinstance(item, EvidenceItem):
            document = documents[item.source_document_id]
            assert item.evidence_metadata["source_version"] == document.version == 1
            assert item.evidence_metadata["source_checksum"] == document.checksum
            assert item.evidence_metadata["trust_status"] == "synthetic"


def test_runtime_fixture_lookup_loads_container_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.modules.demo import fixtures

    fixture_directory = Path(__file__).resolve().parents[4] / "data" / "demo"
    bundled = tmp_path / "app" / "demo_fixtures"
    shutil.copytree(fixture_directory, bundled)
    monkeypatch.setattr(fixtures, "__file__", str(tmp_path / "app/modules/demo/fixtures.py"))

    assert fixtures._fixture_directory() == bundled
    assert set(_load_fixture_bundle(fixtures._fixture_directory())) == set(RUNTIME_FIXTURE_NAMES)


def test_runtime_fixture_bundle_fails_closed_for_missing_or_corrupt_artifacts(
    tmp_path: Path,
) -> None:
    fixture_directory = Path(__file__).resolve().parents[4] / "data" / "demo"

    def copy_runtime_bundle(target: Path) -> None:
        target.mkdir()
        for filename in (*RUNTIME_FIXTURE_NAMES, "manifest-v1.json"):
            shutil.copyfile(fixture_directory / filename, target / filename)

    valid = tmp_path / "valid"
    copy_runtime_bundle(valid)
    assert set(_load_fixture_bundle(valid)) == set(RUNTIME_FIXTURE_NAMES)

    missing = tmp_path / "missing"
    copy_runtime_bundle(missing)
    (missing / "suppliers.json").unlink()
    with pytest.raises(RuntimeError, match="required Maverick demo fixture is unavailable"):
        _load_fixture_bundle(missing)

    corrupt = tmp_path / "corrupt"
    copy_runtime_bundle(corrupt)
    (corrupt / "suppliers.json").write_bytes(b"{}")
    with pytest.raises(RuntimeError, match="failed integrity validation"):
        _load_fixture_bundle(corrupt)


def test_maverick_fixture_generator_replays_byte_for_byte(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[4]
    fixture_directory = repository_root / "data" / "demo"
    subprocess.run(
        [
            sys.executable,
            str(repository_root / "scripts" / "generate_demo_fixtures.py"),
            "--output-directory",
            str(tmp_path),
        ],
        check=True,
        cwd=repository_root,
        timeout=30,
    )
    manifest = json.loads((fixture_directory / "manifest-v1.json").read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        filename = Path(artifact["path"]).name
        assert (tmp_path / filename).read_bytes() == (fixture_directory / filename).read_bytes()
    assert b"\r\n" not in (tmp_path / "electricity-hourly.csv").read_bytes()
    complete = json.loads((fixture_directory / "complete-q3-manifest-v1.json").read_text())
    for name in ["complete-q3-manifest-v1.json", *(Path(item["path"]).name for item in complete["artifacts"])]:
        assert (tmp_path / name).read_bytes() == (fixture_directory / name).read_bytes()

    expected = json.loads((tmp_path / "expected-results.json").read_text(encoding="utf-8"))
    assert expected["fixture_id"] == "maverick-q3-2026-v1"
    assert expected["fixture_generation_method"] == {
        "key": "demo.maverick.fixture_generation",
        "version": "1.0.0",
        "code_version": "carbonmesh-api-0.1.0",
    }
    for filename, expected_hash in expected["fixture_inputs"].items():
        assert hashlib.sha256((tmp_path / filename).read_bytes()).hexdigest() == expected_hash
    assert payload_sha256(expected["fixture_inputs"])[1] == expected["fixture_input_hash"]
    for module, fields in {
        "measurement": ("input_hash", "output_hash"),
        "dispatch": (
            "analysis_signature",
            "optimization_input_hash",
            "optimization_output_hash",
        ),
    }.items():
        for field in fields:
            assert len(expected[module][field]) == 64

    procurement = expected["procurement"]
    frozen_context = procurement["frozen_context"]
    assert procurement["identity_scope"] == "fixture_replay_context"
    assert procurement["stable_replay_ids"] == {
        "carbon_measurement_id": "00000000-0000-4000-8000-000000000701"
    }
    assert set(frozen_context) == {
        "schema_version",
        "company_id",
        "site_id",
        "reporting_period_id",
        "carbon_measurement_id",
        "agent_run_id",
        "actor_id",
        "current_product",
        "current_product_display",
        "quantity",
        "quantity_unit",
        "constraints",
        "scoring_method",
    }
    assert frozen_context["current_product"]["id"] == str(CURRENT_PRODUCT_ID)
    assert frozen_context["scoring_method"]["id"] == str(SCORING_METHOD_ID)
    SupplierProductSummary.model_validate(frozen_context["current_product_display"])
    ScoringMethod.model_validate(frozen_context["scoring_method"])
    assert scenario_context_signature(frozen_context) == procurement["analysis_signature"]
    assert payload_sha256(procurement["scoring_input"])[1] == procurement["scoring_input_hash"]
    assert payload_sha256(procurement["scoring_output"])[1] == procurement["scoring_output_hash"]
    assert procurement["scoring_input"]["frozen_context"] == frozen_context
    assert procurement["scoring_output"] == {
        "selected_product": {
            "id": str(ALTERNATIVE_B_PRODUCT_ID),
            "code": "AL-RECYCLED-B",
        },
        "assessments": procurement["assessments"],
    }

    supplier_fixture = json.loads((tmp_path / "suppliers.json").read_text(encoding="utf-8"))
    suppliers_by_code = {item["product_code"]: item for item in supplier_fixture["products"]}
    golden_product_ids = {
        "AL-RECYCLED-A": ALTERNATIVE_A_PRODUCT_ID,
        "AL-RECYCLED-B": ALTERNATIVE_B_PRODUCT_ID,
        "AL-RECYCLED-C": ALTERNATIVE_C_PRODUCT_ID,
    }
    for code, product_id in golden_product_ids.items():
        generated_input = procurement["scoring_input"]["candidate_products"][code]
        source = suppliers_by_code[code]
        assert generated_input["product_id"] == str(product_id)
        assert generated_input["pcf_kgco2e_per_unit"] == source["pcf_kgco2e_per_unit"]
        assert generated_input["circularity_score"] == source["circularity_score"]
        assert generated_input["evidence_quality_score"] == source["evidence_quality_score"]
        assert generated_input["unit_cost"] == source["unit_cost"]


def test_demo_reset_truncates_only_the_authoritative_table_catalogue() -> None:
    statement = _truncate_statement()

    assert statement.startswith("TRUNCATE TABLE ")
    assert statement.endswith(" RESTART IDENTITY RESTRICT")
    assert statement.count('"."') == 46
    assert '"core"."companies"' in statement
    assert '"ledger"."ledger_events"' in statement


def test_demo_reset_route_returns_stable_seed_context(monkeypatch) -> None:
    fake_session = object()

    async def fake_reset(session: object) -> DemoResetSummary:
        assert session is fake_session
        return DemoResetSummary(
            company_id=DEMO_COMPANY_ID,
            site_id=DEMO_SITE_ID,
            reporting_period_id=DEMO_PERIOD_ID,
            metric_count=9,
            activity_record_count=1,
            supplier_product_count=4,
            emission_factor_count=1,
        )

    monkeypatch.setattr("app.api.routes.demo.reset_and_seed_demo", fake_reset)
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/demo")
    test_app.dependency_overrides[get_db_session] = lambda: fake_session
    test_app.dependency_overrides[authorize_demo_reset] = lambda: None

    response = TestClient(test_app).post("/api/demo/reset")

    assert response.status_code == 200
    assert response.json() == {
        "status": "reset",
        "synthetic": True,
        "company_id": str(DEMO_COMPANY_ID),
        "site_id": str(DEMO_SITE_ID),
        "reporting_period_id": str(DEMO_PERIOD_ID),
        "seeded": {
            "metrics": 9,
            "activity_records": 1,
            "supplier_products": 4,
            "emission_factors": 1,
        },
    }
