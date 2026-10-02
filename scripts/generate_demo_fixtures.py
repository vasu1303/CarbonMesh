"""Generate the versioned, fully synthetic Maverick Manufacturing demo inputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
DEMO = ROOT / "data" / "demo"
GOLDEN_INPUT_NAMES = (
    "activity.csv",
    "electricity-hourly.csv",
    "suppliers.json",
    "factor-evidence.txt",
    "assurance-standard.json",
    "assurance-evidence.md",
    "grid-history.json",
    "grid-forecast-6h.json",
    "grid-forecast-24h.json",
    "grid-forecast-48h.json",
    "grid-forecast-72h.json",
)
DEMO_ARTIFACT_NAMES = (
    *GOLDEN_INPUT_NAMES,
    "expected-results.json",
    "api_e2e_expected.json",
)
START = datetime(2026, 7, 1, tzinfo=UTC)
HOURS = 90 * 24
ZONE = "IN"
FORECAST_START = datetime(2026, 10, 1, 8, tzinfo=UTC)
FORECAST_ISSUED_AT = datetime(2026, 10, 1, 7, 30, tzinfo=UTC)
MISSING_ACTIVITY_AT = datetime(2026, 8, 15, 3, tzinfo=UTC)
DUPLICATE_ACTIVITY_AT = datetime(2026, 8, 20, 14, tzinfo=UTC)
MISSING_GRID_AT = datetime(2026, 9, 1, 5, tzinfo=UTC)
ESTIMATED_GRID_AT = datetime(2026, 8, 10, 12, tzinfo=UTC)


def iso_z(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_hash(payload: dict[str, Any]) -> str:
    """Use the application's canonical ledger hasher for golden identities."""

    from app.modules.ledger.service import payload_sha256

    return payload_sha256(payload)[1]


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def electricity_kwh(timestamp: datetime) -> Decimal:
    hour = timestamp.hour
    weekday_adjustment = Decimal(24) if timestamp.weekday() < 5 else Decimal(-18)
    shift_adjustment = Decimal(95) if 7 <= hour < 19 else Decimal(35)
    day_cycle = Decimal((timestamp.timetuple().tm_yday % 11) * 3)
    return Decimal(410) + weekday_adjustment + shift_adjustment + day_cycle


def grid_intensity(timestamp: datetime) -> Decimal:
    hourly_profile = (
        535,
        525,
        515,
        505,
        495,
        485,
        470,
        455,
        440,
        425,
        405,
        390,
        380,
        375,
        385,
        410,
        445,
        480,
        515,
        545,
        565,
        575,
        565,
        550,
    )
    return Decimal(hourly_profile[timestamp.hour] + (timestamp.timetuple().tm_yday % 7) * 2)


def build_electricity_fixture() -> tuple[int, Decimal]:
    rows: list[dict[str, str]] = []
    valid_scope2 = Decimal(0)
    for offset in range(HOURS):
        timestamp = START + timedelta(hours=offset)
        if timestamp == MISSING_ACTIVITY_AT:
            continue
        row = {
            "row_key": f"plant-b-electricity-{timestamp:%Y%m%dT%H%MZ}",
            "timestamp": iso_z(timestamp),
            "kwh": format(electricity_kwh(timestamp), "f"),
            "unit": "kWh",
            "site_code": "PLANT-B",
            "synthetic": "true",
            "data_quality_case": (
                "duplicate_timestamp" if timestamp == DUPLICATE_ACTIVITY_AT else ""
            ),
        }
        rows.append(row)
        if timestamp not in {DUPLICATE_ACTIVITY_AT, MISSING_GRID_AT}:
            valid_scope2 += electricity_kwh(timestamp) * grid_intensity(timestamp) / Decimal(1000)
        if timestamp == DUPLICATE_ACTIVITY_AT:
            rows.append(
                {
                    **row,
                    "row_key": f"{row['row_key']}-DUPLICATE",
                    "data_quality_case": "duplicate_timestamp",
                }
            )

    path = DEMO / "electricity-hourly.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows), valid_scope2.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def build_history_fixture() -> int:
    points: list[dict[str, Any]] = []
    for offset in range(HOURS):
        timestamp = START + timedelta(hours=offset)
        if timestamp == MISSING_GRID_AT:
            continue
        estimated = timestamp == ESTIMATED_GRID_AT
        points.append(
            {
                "zone": ZONE,
                "carbonIntensity": int(grid_intensity(timestamp)),
                "datetime": iso_z(timestamp),
                "updatedAt": iso_z(timestamp + timedelta(minutes=20)),
                "createdAt": iso_z(timestamp + timedelta(minutes=10)),
                "emissionFactorType": "lifecycle",
                "flowTraced": True,
                "isEstimated": estimated,
                "estimationMethod": "SYNTHETIC_GAP_FILL" if estimated else None,
                "temporalGranularity": "hourly",
            }
        )
    write_json(
        DEMO / "grid-history.json",
        {
            "synthetic": True,
            "fixtureVersion": "maverick-q3-2026-v1",
            "zone": ZONE,
            "temporalGranularity": "hourly",
            "aggregationPeriod": "hourly",
            "data": points,
        },
    )
    return len(points)


def build_complete_quarter_fixtures() -> None:
    """Generate a separate complete quarter; never repair the quality-case inputs."""
    end = datetime(2026, 10, 1, tzinfo=UTC)
    hours = int((end - START).total_seconds() // 3600)
    fixture_id = "maverick-q3-2026-complete-v1"
    activity_name = "electricity-hourly-complete-q3-v1.csv"
    material_name = "activity-material-complete-q3-v1.csv"
    history_name = "grid-history-complete-q3-v1.json"
    rows = []
    points = []
    total_kwh = Decimal(0)
    total_kgco2e = Decimal(0)
    for offset in range(hours):
        timestamp = START + timedelta(hours=offset)
        kwh = electricity_kwh(timestamp)
        intensity = grid_intensity(timestamp)
        total_kwh += kwh
        total_kgco2e += kwh * intensity / Decimal(1000)
        rows.append({
            "row_key": f"complete-q3-v1-electricity-{timestamp:%Y%m%dT%H%MZ}",
            "timestamp": iso_z(timestamp),
            "kwh": format(kwh, "f"),
            "unit": "kWh",
            "site_code": "PLANT-B",
            "synthetic": "true",
            "fixture_version": fixture_id,
        })
        points.append({
            "zone": ZONE,
            "carbonIntensity": int(intensity),
            "datetime": iso_z(timestamp),
            "updatedAt": iso_z(timestamp + timedelta(minutes=20)),
            "createdAt": iso_z(timestamp + timedelta(minutes=10)),
            "emissionFactorType": "lifecycle",
            "flowTraced": True,
            "isEstimated": timestamp == ESTIMATED_GRID_AT,
            "estimationMethod": "SYNTHETIC_GAP_FILL" if timestamp == ESTIMATED_GRID_AT else None,
            "temporalGranularity": "hourly",
        })
    with (DEMO / activity_name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    write_json(DEMO / history_name, {
        "synthetic": True,
        "fixtureVersion": fixture_id,
        "zone": ZONE,
        "temporalGranularity": "hourly",
        "aggregationPeriod": "hourly",
        "data": points,
    })
    with (DEMO / "activity.csv").open(encoding="utf-8", newline="") as handle:
        material_rows = list(csv.DictReader(handle))
    for row in material_rows:
        row["row_key"] = f"complete-q3-v1-{row['row_key']}"
    with (DEMO / material_name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(material_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(material_rows)
    write_json(DEMO / "complete-q3-manifest-v1.json", {
        "manifest_version": "1.0.0",
        "fixture_id": fixture_id,
        "synthetic": True,
        "generated_by": "scripts/generate_demo_fixtures.py",
        "company": "Maverick Manufacturing (synthetic)",
        "site": "Plant B (synthetic)",
        "reporting_period": "Q3 2026",
        "interval_start": iso_z(START),
        "interval_end": iso_z(end),
        "hourly_intervals": hours,
        "fixture_variant": "complete_q3_v1",
        "expected": {
            "electricity_kwh": format(total_kwh, "f"),
            "scope2_kgco2e": format(total_kgco2e.quantize(Decimal("0.000001")), "f"),
            "formula": "sum(kWh * gCO2e_per_kWh / 1000)",
            "method_key": "measurement.scope2.location_based.hourly",
            "method_version": "2.0.0",
            "full_reporting_period": True,
        },
        "artifacts": [
            {"path": f"data/demo/{name}", "sha256": sha256_bytes((DEMO / name).read_bytes())}
            for name in (activity_name, material_name, history_name)
        ],
        "quality_cases_fixture": "maverick-q3-2026-v1",
    })


def forecast_intensity(index: int) -> int:
    opening = [480, 460, 420, 300, 280, 350]
    if index < len(opening):
        return opening[index]
    return 390 + ((index * 37) % 170)


def build_forecast_fixtures() -> None:
    for horizon in (6, 24, 48, 72):
        write_json(
            DEMO / f"grid-forecast-{horizon}h.json",
            {
                "synthetic": True,
                "fixtureVersion": "maverick-q3-2026-v1",
                "zone": ZONE,
                "forecast": [
                    {
                        "carbonIntensity": forecast_intensity(index),
                        "datetime": iso_z(FORECAST_START + timedelta(hours=index)),
                    }
                    for index in range(horizon)
                ],
                "updatedAt": iso_z(FORECAST_ISSUED_AT),
                "temporalGranularity": "hourly",
            },
        )


def build_purchased_material_fixture() -> None:
    activity = (
        "row_key,material_code,quantity,unit,activity_date,supplier_code,"
        "product_code,unit_cost,currency,synthetic\n"
        "plant-b-recycled-aluminium-q3,RECYCLED-ALUMINIUM,10000,kg,2026-09-15,"
        "MAVERICK-CURRENT,AL-CURRENT,2.500000,USD,true\n"
    )
    (DEMO / "activity.csv").write_text(activity, encoding="utf-8", newline="\n")

    products = [
        {
            "supplier_code": "MAVERICK-CURRENT",
            "supplier_name": "Maverick Metals Baseline (Synthetic)",
            "country_code": "IN",
            "product_code": "AL-CURRENT",
            "name": "Current recycled aluminium billet",
            "description": "Synthetic current product for the Plant B demo.",
            "material_code": "RECYCLED-ALUMINIUM",
            "category": "metals",
            "pcf_kgco2e_per_unit": "8.6",
            "pcf_unit": "kgCO2e/kg",
            "circularity_score": "62",
            "recycled_content_pct": "60",
            "recyclable_pct": "98",
            "evidence_quality_score": "94",
            "lead_time_days": 10,
            "unit_cost": "2.500000",
            "currency": "USD",
            "effective_from": "2026-01-01",
            "supplier_metadata": {"synthetic": True, "risk": "low"},
            "evidence_text": "Synthetic product PCF: 8.6 kgCO2e/kg; recycled content: 60%.",
        },
        {
            "supplier_code": "CIRCULAR-AL-A",
            "supplier_name": "Circular Aluminium A (Synthetic)",
            "country_code": "IN",
            "product_code": "AL-RECYCLED-A",
            "name": "Recycled aluminium billet A",
            "description": "Synthetic lower-carbon comparison product.",
            "material_code": "RECYCLED-ALUMINIUM",
            "category": "metals",
            "pcf_kgco2e_per_unit": "4.2",
            "pcf_unit": "kgCO2e/kg",
            "circularity_score": "82",
            "recycled_content_pct": "82",
            "recyclable_pct": "99",
            "evidence_quality_score": "88",
            "lead_time_days": 12,
            "unit_cost": "2.550000",
            "currency": "USD",
            "effective_from": "2026-01-01",
            "supplier_metadata": {"synthetic": True, "risk": "low"},
            "evidence_text": "Synthetic product PCF: 4.2 kgCO2e/kg; recycled content: 82%.",
        },
        {
            "supplier_code": "CIRCULAR-AL-B",
            "supplier_name": "Circular Aluminium B (Synthetic)",
            "country_code": "IN",
            "product_code": "AL-RECYCLED-B",
            "name": "High-recycled aluminium billet B",
            "description": "Synthetic recommended lower-carbon comparison product.",
            "material_code": "RECYCLED-ALUMINIUM",
            "category": "metals",
            "pcf_kgco2e_per_unit": "2.4",
            "pcf_unit": "kgCO2e/kg",
            "circularity_score": "94",
            "recycled_content_pct": "95",
            "recyclable_pct": "99",
            "evidence_quality_score": "92",
            "lead_time_days": 14,
            "unit_cost": "2.600000",
            "currency": "USD",
            "effective_from": "2026-01-01",
            "supplier_metadata": {"synthetic": True, "risk": "low"},
            "evidence_text": "Synthetic product PCF: 2.4 kgCO2e/kg; recycled content: 95%.",
        },
        {
            "supplier_code": "CIRCULAR-AL-C",
            "supplier_name": "Premium Circular Aluminium C (Synthetic)",
            "country_code": "IN",
            "product_code": "AL-RECYCLED-C",
            "name": "Premium recycled aluminium billet C",
            "description": "Synthetic low-carbon product above the hard cost ceiling.",
            "material_code": "RECYCLED-ALUMINIUM",
            "category": "metals",
            "pcf_kgco2e_per_unit": "1.9",
            "pcf_unit": "kgCO2e/kg",
            "circularity_score": "97",
            "recycled_content_pct": "98",
            "recyclable_pct": "100",
            "evidence_quality_score": "95",
            "lead_time_days": 13,
            "unit_cost": "2.725000",
            "currency": "USD",
            "effective_from": "2026-01-01",
            "supplier_metadata": {"synthetic": True, "risk": "medium"},
            "evidence_text": "Synthetic product PCF: 1.9 kgCO2e/kg; cost exceeds the demo ceiling.",
        },
    ]
    write_json(
        DEMO / "suppliers.json",
        {
            "synthetic": True,
            "fixture_version": "maverick-q3-2026-v1",
            "company": "Maverick Manufacturing (synthetic)",
            "products": products,
        },
    )

    (DEMO / "factor-evidence.txt").write_text(
        "SYNTHETIC DEMO EVIDENCE - NOT THIRD-PARTY VERIFIED\n\n"
        "Product-specific cradle-to-gate factor for AL-CURRENT:\n"
        "8.6 kgCO2e/kg, effective 2026-01-01 for India.\n",
        encoding="utf-8",
        newline="\n",
    )


def build_assurance_fixtures() -> None:
    write_json(
        DEMO / "assurance-standard.json",
        {
            "synthetic": True,
            "fixture_version": "maverick-q3-2026-v1",
            "code": "GHG-PROTOCOL-SCOPE-2-DEMO",
            "version": "2026-demo-v1",
            "name": "GHG Protocol Scope 2 summary with limited ESRS mapping (POC draft)",
            "requirements": [
                {
                    "code": "S2-BOUNDARY",
                    "title": "Organizational and site boundary",
                    "claim_template": "{company} reports location-based Scope 2 for {site}.",
                },
                {
                    "code": "S2-TOTAL",
                    "title": "Location-based Scope 2 total",
                    "claim_template": "Location-based Scope 2 emissions were {scope2_total}.",
                },
                {
                    "code": "ESRS-E1-TREND-LIMITED",
                    "title": "Limited ESRS-style change claim",
                    "claim_template": "Scope 2 emissions fell {reduction_pct} year over year.",
                },
            ],
        },
    )
    (DEMO / "assurance-evidence.md").write_text(
        "# Maverick Manufacturing - synthetic evidence\n\n"
        "All values and entities in this file are synthetic and for the CarbonMesh demo only.\n\n"
        "Plant B is the reporting boundary for Q3 2026. Electricity activity is recorded hourly "
        "in kWh. Location-based factors use lifecycle, flow-traced hourly grid intensity.\n\n"
        "No prior-period verified Scope 2 total is supplied. A year-over-year reduction claim is "
        "therefore unsupported and must be blocked. This is a POC draft, not an assurance opinion "
        "or filing.\n",
        encoding="utf-8",
        newline="\n",
    )


def procurement_golden() -> tuple[
    str,
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    from uuid import UUID

    from app.db.models.procurement import Supplier, SupplierProduct
    from app.db.models.semantic import MethodDefinition
    from app.modules.demo.fixtures import (
        ALTERNATIVE_A_EVIDENCE_ID,
        ALTERNATIVE_A_PRODUCT_ID,
        ALTERNATIVE_A_SUPPLIER_ID,
        ALTERNATIVE_B_EVIDENCE_ID,
        ALTERNATIVE_B_PRODUCT_ID,
        ALTERNATIVE_B_SUPPLIER_ID,
        ALTERNATIVE_C_EVIDENCE_ID,
        ALTERNATIVE_C_PRODUCT_ID,
        ALTERNATIVE_C_SUPPLIER_ID,
        CURRENT_PRODUCT_EVIDENCE_ID,
        CURRENT_PRODUCT_ID,
        CURRENT_SUPPLIER_ID,
        DEMO_COMPANY_ID,
        DEMO_PERIOD_ID,
        DEMO_PROCUREMENT_MANAGER_ID,
        DEMO_SITE_ID,
        SCORING_METHOD_ID,
    )
    from app.modules.procurement.repository import ProductRecord
    from app.modules.procurement.review import (
        canonicalize_scenario_context,
        scenario_context_signature,
    )
    from app.modules.procurement.schemas import (
        CreateScenarioRequest,
        MaterialConstraints,
        ScoringWeights,
    )
    from app.modules.procurement.scoring import (
        ProductFacts,
        ScenarioFacts,
        calculate_assessment,
    )
    from app.modules.procurement.service import ProcurementService

    fixture = json.loads((DEMO / "suppliers.json").read_text(encoding="utf-8"))
    fixture_products = fixture.get("products") if isinstance(fixture, dict) else None
    if (
        not isinstance(fixture_products, list)
        or fixture.get("synthetic") is not True
        or fixture.get("fixture_version") != "maverick-q3-2026-v1"
    ):
        raise RuntimeError("The generated supplier fixture contract is invalid.")

    products_by_code = {
        item["product_code"]: item
        for item in fixture_products
        if isinstance(item, dict) and isinstance(item.get("product_code"), str)
    }
    expected_codes = {
        "AL-CURRENT",
        "AL-RECYCLED-A",
        "AL-RECYCLED-B",
        "AL-RECYCLED-C",
    }
    if set(products_by_code) != expected_codes or len(fixture_products) != len(expected_codes):
        raise RuntimeError("The generated supplier fixture product catalog is invalid.")

    supplier_ids = {
        "AL-CURRENT": CURRENT_SUPPLIER_ID,
        "AL-RECYCLED-A": ALTERNATIVE_A_SUPPLIER_ID,
        "AL-RECYCLED-B": ALTERNATIVE_B_SUPPLIER_ID,
        "AL-RECYCLED-C": ALTERNATIVE_C_SUPPLIER_ID,
    }
    product_ids = {
        "AL-CURRENT": CURRENT_PRODUCT_ID,
        "AL-RECYCLED-A": ALTERNATIVE_A_PRODUCT_ID,
        "AL-RECYCLED-B": ALTERNATIVE_B_PRODUCT_ID,
        "AL-RECYCLED-C": ALTERNATIVE_C_PRODUCT_ID,
    }
    evidence_ids = {
        "AL-CURRENT": CURRENT_PRODUCT_EVIDENCE_ID,
        "AL-RECYCLED-A": ALTERNATIVE_A_EVIDENCE_ID,
        "AL-RECYCLED-B": ALTERNATIVE_B_EVIDENCE_ID,
        "AL-RECYCLED-C": ALTERNATIVE_C_EVIDENCE_ID,
    }

    stable_timestamp = datetime(2026, 1, 1, tzinfo=UTC)

    def product_record(code: str) -> ProductRecord:
        source = products_by_code[code]
        supplier = Supplier(
            id=supplier_ids[code],
            company_id=DEMO_COMPANY_ID,
            supplier_code=source["supplier_code"],
            name=source["supplier_name"],
            country_code=source["country_code"],
            status="active",
            supplier_metadata=source["supplier_metadata"],
        )
        product = SupplierProduct(
            id=product_ids[code],
            company_id=DEMO_COMPANY_ID,
            supplier_id=supplier_ids[code],
            evidence_item_id=evidence_ids[code],
            product_code=code,
            name=source["name"],
            material_code=source["material_code"],
            category=source["category"],
            description=source.get("description"),
            pcf_kgco2e_per_unit=Decimal(source["pcf_kgco2e_per_unit"]),
            pcf_unit=source["pcf_unit"],
            circularity_score=Decimal(source["circularity_score"]),
            recycled_content_pct=Decimal(source["recycled_content_pct"]),
            recyclable_pct=Decimal(source["recyclable_pct"]),
            evidence_quality_score=Decimal(source["evidence_quality_score"]),
            lead_time_days=source["lead_time_days"],
            unit_cost=Decimal(source["unit_cost"]),
            currency=source["currency"],
            effective_from=date.fromisoformat(source["effective_from"]),
            effective_to=(
                date.fromisoformat(source["effective_to"]) if source.get("effective_to") else None
            ),
            is_active=source.get("is_active", True),
        )
        product.created_at = stable_timestamp
        product.updated_at = stable_timestamp
        return ProductRecord(product=product, supplier=supplier)

    records = {code: product_record(code) for code in sorted(expected_codes)}
    service = ProcurementService.__new__(ProcurementService)
    facts = {code: service._product_facts(record) for code, record in records.items()}
    current = facts["AL-CURRENT"]

    scenario = ScenarioFacts(
        quantity=Decimal(10000),
        current_unit_cost=current.unit_cost,
        currency=current.currency,
        max_cost_increase_pct=Decimal(5),
        max_lead_time_days=20,
        minimum_circularity_score=Decimal(75),
        material_constraints=MaterialConstraints(allowed_material_codes=["RECYCLED-ALUMINIUM"]),
        period_start=datetime(2026, 7, 1, tzinfo=UTC).date(),
        period_end=datetime(2026, 9, 30, tzinfo=UTC).date(),
        carbon_weight=Decimal("0.40"),
        evidence_weight=Decimal("0.25"),
        circularity_weight=Decimal("0.20"),
        operational_fit_weight=Decimal("0.15"),
    )
    assessments: dict[str, dict[str, Any]] = {}
    raw_assessments = {}
    for code in sorted(expected_codes - {"AL-CURRENT"}):
        calculated = calculate_assessment(
            current=current,
            candidate=facts[code],
            scenario=scenario,
        )
        raw_assessments[code] = calculated
        assessments[code] = {
            "product_id": str(product_ids[code]),
            "feasible": calculated.feasible,
            "scores": {
                "carbon": format(calculated.carbon_score, "f"),
                "evidence": format(calculated.evidence_score, "f"),
                "circularity": format(calculated.circularity_score, "f"),
                "operational_fit": format(calculated.operational_fit_score, "f"),
                "total": format(calculated.total_score, "f"),
            },
            "total_score": format(calculated.total_score, "f"),
            "projected_footprint_kgco2e": format(calculated.projected_footprint_kgco2e, "f"),
            "avoided_kgco2e": format(calculated.avoided_kgco2e, "f"),
            "reduction_pct": format(calculated.reduction_pct, "f"),
            "cost_delta_pct": format(calculated.cost_delta_pct, "f"),
            "lead_time_delta_days": calculated.lead_time_delta_days,
            "infeasibility_reasons": calculated.infeasibility_reasons,
            "infeasibility_codes": [
                str(reason["code"]) for reason in calculated.infeasibility_reasons
            ],
        }
    selected_code = min(
        (code for code, item in raw_assessments.items() if item.feasible),
        key=lambda code: (
            -raw_assessments[code].total_score,
            raw_assessments[code].projected_footprint_kgco2e,
            facts[code].unit_cost,
            str(product_ids[code]),
        ),
    )
    selected = assessments[selected_code]

    weights = ScoringWeights(
        carbon=Decimal("0.40"),
        evidence=Decimal("0.25"),
        circularity=Decimal("0.20"),
        operational_fit=Decimal("0.15"),
    )
    method = MethodDefinition(
        id=SCORING_METHOD_ID,
        company_id=DEMO_COMPANY_ID,
        method_type="supplier_scoring",
        key="procurement.supplier_scoring",
        version="1.0.0",
        name="CarbonMesh supplier scoring",
        code_version="carbonmesh-api-0.1.0",
        configuration=weights.model_dump(mode="json"),
        effective_from=date(2026, 1, 1),
        is_active=True,
    )
    golden_measurement_id = UUID("00000000-0000-4000-8000-000000000701")
    request = CreateScenarioRequest(
        company_id=DEMO_COMPANY_ID,
        site_id=DEMO_SITE_ID,
        reporting_period_id=DEMO_PERIOD_ID,
        current_product_id=CURRENT_PRODUCT_ID,
        carbon_measurement_id=golden_measurement_id,
        method_definition_id=SCORING_METHOD_ID,
        requested_by=DEMO_PROCUREMENT_MANAGER_ID,
        quantity=Decimal(10000),
        quantity_unit="kg",
        current_unit_cost=current.unit_cost,
        currency=current.currency,
        max_cost_increase_pct=Decimal(5),
        max_lead_time_days=20,
        minimum_circularity_score=Decimal(75),
        material_constraints=MaterialConstraints(allowed_material_codes=["RECYCLED-ALUMINIUM"]),
    )
    analysis_context = canonicalize_scenario_context(
        service._frozen_context(
            request=request,
            current_product=records["AL-CURRENT"],
            current_unit_cost=current.unit_cost,
            currency=current.currency,
            weights=weights,
            method=method,
        )
    )

    def fact_snapshot(code: str, product: ProductFacts) -> dict[str, Any]:
        return {
            "product_id": str(product_ids[code]),
            "supplier_id": str(supplier_ids[code]),
            "evidence_item_id": str(evidence_ids[code]),
            "material_code": product.material_code,
            "category": product.category,
            "pcf_kgco2e_per_unit": format(product.pcf_kgco2e_per_unit, "f"),
            "pcf_unit": product.pcf_unit,
            "circularity_score": format(product.circularity_score, "f"),
            "evidence_quality_score": format(product.evidence_quality_score, "f"),
            "lead_time_days": product.lead_time_days,
            "unit_cost": format(product.unit_cost, "f"),
            "currency": product.currency,
            "effective_from": product.effective_from.isoformat(),
            "effective_to": (product.effective_to.isoformat() if product.effective_to else None),
            "supplier_risk": product.supplier_risk,
        }

    scoring_input = {
        "frozen_context": analysis_context,
        "current_product": fact_snapshot("AL-CURRENT", current),
        "candidate_products": {
            code: fact_snapshot(code, facts[code])
            for code in sorted(expected_codes - {"AL-CURRENT"})
        },
    }
    scoring_output = {
        "selected_product": {
            "id": str(product_ids[selected_code]),
            "code": selected_code,
        },
        "assessments": assessments,
    }
    identities = {
        "identity_scope": "fixture_replay_context",
        "stable_replay_ids": {
            "carbon_measurement_id": str(golden_measurement_id),
        },
        "frozen_context": analysis_context,
        "scoring_input": scoring_input,
        "scoring_output": scoring_output,
        "analysis_signature": scenario_context_signature(analysis_context),
        "scoring_input_hash": canonical_hash(scoring_input),
        "scoring_output_hash": canonical_hash(scoring_output),
    }
    return selected_code, selected, assessments, identities


def dispatch_golden():
    from app.modules.dispatch.optimization import (
        CapacityWindow,
        DispatchInputs,
        ForecastValue,
        TimeWindow,
        optimize_dispatch,
    )

    forecast = tuple(
        ForecastValue(
            forecast_for=FORECAST_START + timedelta(hours=index),
            intensity_gco2e_per_kwh=Decimal(forecast_intensity(index)),
        )
        for index in range(24)
    )
    inputs = DispatchInputs(
        power_kw=Decimal(500),
        energy_kwh=Decimal(1000),
        duration_minutes=120,
        earliest_start=FORECAST_START,
        latest_finish=FORECAST_START + timedelta(hours=12),
        baseline_start=FORECAST_START,
        maximum_delay_minutes=240,
        blackouts=(
            TimeWindow(
                FORECAST_START + timedelta(hours=2),
                FORECAST_START + timedelta(hours=3),
            ),
        ),
        capacity_windows=(
            CapacityWindow(
                available_capacity_kw=Decimal(500),
                start=FORECAST_START,
                end=FORECAST_START + timedelta(hours=12),
            ),
        ),
    )
    optimized = optimize_dispatch(inputs, forecast)
    point_hashes = [
        canonical_hash(
            {
                "provider": "electricity_maps",
                "api_version": "v4",
                "zone": ZONE,
                "forecast_for": point.forecast_for,
                "issued_at": FORECAST_ISSUED_AT,
                "intensity_gco2e_per_kwh": point.intensity_gco2e_per_kwh,
                "emission_factor_type": "lifecycle",
                "flow_traced": True,
                "is_estimated": True,
                "estimation_method": "electricity_maps_forecast",
                "temporal_granularity": "hourly",
            }
        )
        for point in forecast
    ]
    context = {
        "company_id": "00000000-0000-4000-8000-000000000001",
        "site_id": "00000000-0000-4000-8000-000000000002",
        "flexible_load_id": "00000000-0000-4000-8000-000000000501",
        "requested_by": "00000000-0000-4000-8000-000000000006",
        "window_start": FORECAST_START,
        "window_end": FORECAST_START + timedelta(hours=12),
        "objective": "minimum_carbon",
    }
    context_hash = canonical_hash(context)
    constraint_payload = {
        "earliest_start": inputs.earliest_start,
        "latest_finish": inputs.latest_finish,
        "baseline_start": inputs.baseline_start,
        "maximum_delay_minutes": inputs.maximum_delay_minutes,
        "blackouts": [{"start": item.start, "end": item.end} for item in inputs.blackouts],
        "capacity_windows": [
            {
                "available_capacity_kw": item.available_capacity_kw,
                "start": item.start,
                "end": item.end,
            }
            for item in inputs.capacity_windows
        ],
        "source_constraint_ids": [
            "00000000-0000-4000-8000-000000000502",
            "00000000-0000-4000-8000-000000000503",
            "00000000-0000-4000-8000-000000000504",
            "00000000-0000-4000-8000-000000000505",
        ],
    }
    signature_input = {
        "context_hash": context_hash,
        "load": {
            "power_kw": inputs.power_kw,
            "duration_minutes": inputs.duration_minutes,
            "energy_kwh": inputs.energy_kwh,
        },
        "method": {
            "id": "00000000-0000-4000-8000-000000000125",
            "key": "dispatch.minimum_carbon.consecutive_windows",
            "version": "1.0.0",
            "code_version": "carbonmesh-api-0.1.0",
            "configuration": {
                "objective": "minimum_carbon",
                "tie_break": "earliest_feasible_start",
                "missing_interval_policy": "invalidate_candidate",
                "actuation_authorized": False,
            },
        },
        "policy": {
            "id": "00000000-0000-4000-8000-000000000126",
            "key": "dispatch.advisory_only",
            "version": "1.0.0",
            "configuration": {
                "actuation_authorized": False,
                "approval_required": True,
            },
        },
        "constraints": constraint_payload,
        "forecast_point_hashes": point_hashes,
    }
    output = {
        "baseline": {
            "start": optimized.baseline.start,
            "end": optimized.baseline.end,
            "emissions_kgco2e": optimized.baseline.emissions_kgco2e,
        },
        "recommended": (
            {
                "start": optimized.recommended.start,
                "end": optimized.recommended.end,
                "emissions_kgco2e": optimized.recommended.emissions_kgco2e,
            }
            if optimized.recommended is not None
            else None
        ),
        "avoided_kgco2e": optimized.avoided_kgco2e,
        "reduction_pct": optimized.reduction_pct,
        "feasible_windows": len(optimized.feasible_windows),
        "rejected_windows": len(optimized.rejected_windows),
        "actuation_authorized": False,
    }
    identities = {
        "context_hash": context_hash,
        "analysis_signature": canonical_hash(signature_input),
        "optimization_input_hash": canonical_hash(signature_input),
        "optimization_output_hash": canonical_hash(output),
    }
    return optimized, identities


def build_expected_results(electricity_rows: int, scope2_total: Decimal) -> dict[str, Any]:
    (
        selected_code,
        selected_procurement,
        procurement_assessments,
        procurement_identities,
    ) = procurement_golden()
    dispatch, dispatch_identities = dispatch_golden()
    if dispatch.recommended is None:
        raise RuntimeError("The canonical Dispatch fixture must have a feasible window.")
    fixture_inputs = {
        filename: sha256_bytes((DEMO / filename).read_bytes()) for filename in GOLDEN_INPUT_NAMES
    }
    expected = {
        "manifest_version": "1.0.0",
        "fixture_id": "maverick-q3-2026-v1",
        "fixture_generation_method": {
            "key": "demo.maverick.fixture_generation",
            "version": "1.0.0",
            "code_version": "carbonmesh-api-0.1.0",
        },
        "fixture_inputs": fixture_inputs,
        "fixture_input_hash": canonical_hash(fixture_inputs),
        "synthetic": True,
        "company": "Maverick Manufacturing (synthetic)",
        "site": "Plant B (synthetic)",
        "reporting_period": "Q3 2026",
        "measurement": {
            "method": {
                "id": "00000000-0000-4000-8000-000000000124",
                "key": "measurement.scope2.location_based.hourly",
                "version": "1.0.0",
                "code_version": "carbonmesh-api-0.1.0",
            },
            "purchased_material": {
                "material_code": "RECYCLED-ALUMINIUM",
                "quantity_kg": "10000",
                "factor_kgco2e_per_kg": "8.6",
                "emissions_kgco2e": "86000.000000",
            },
            "scope2": {
                "input_rows": electricity_rows,
                "expected_hourly_intervals": HOURS,
                "valid_aligned_intervals": HOURS - 3,
                "emissions_kgco2e": format(scope2_total, "f"),
                "formula": "kWh * gCO2e_per_kWh / 1000",
                "confidence_method_version": "measurement-confidence-v2",
            },
            "quality_issues": {
                "missing_activity_intervals": 1,
                "duplicate_activity_timestamps": 1,
                "missing_grid_intervals": 1,
                "estimated_grid_points": 1,
            },
            "input_hash": canonical_hash(
                {
                    "fixture_id": "maverick-q3-2026-v1",
                    "activity_sha256": sha256_bytes((DEMO / "electricity-hourly.csv").read_bytes()),
                    "grid_history_sha256": sha256_bytes((DEMO / "grid-history.json").read_bytes()),
                    "method_id": "00000000-0000-4000-8000-000000000124",
                }
            ),
            "output_hash": canonical_hash(
                {
                    "scope2_emissions_kgco2e": scope2_total,
                    "valid_aligned_intervals": HOURS - 3,
                    "quality_issues": {
                        "missing_activity_intervals": 1,
                        "duplicate_activity_timestamps": 1,
                        "missing_grid_intervals": 1,
                        "estimated_grid_points": 1,
                    },
                }
            ),
        },
        "assurance": {
            "template_code": "GHG-PROTOCOL-SCOPE-2-DEMO",
            "supported_claim_codes": ["S2-BOUNDARY", "S2-TOTAL"],
            "blocked_claim_codes": ["ESRS-E1-TREND-LIMITED"],
            "terminal_state": "unsupported",
            "disclaimer": "POC draft; not an assurance opinion or filing.",
        },
        "procurement": {
            "method": {
                "id": "00000000-0000-4000-8000-000000000123",
                "key": "procurement.supplier_scoring",
                "version": "1.0.0",
                "code_version": "carbonmesh-api-0.1.0",
            },
            **procurement_identities,
            "quantity_kg": "10000",
            "baseline_product_code": "AL-CURRENT",
            "recommended_product_code": selected_code,
            "baseline_footprint_kgco2e": "86000.000000",
            "projected_footprint_kgco2e": selected_procurement["projected_footprint_kgco2e"],
            "avoided_kgco2e": selected_procurement["avoided_kgco2e"],
            "reduction_pct": selected_procurement["reduction_pct"],
            "cost_delta_pct": selected_procurement["cost_delta_pct"],
            "maximum_cost_increase_pct": "5",
            "assessments": procurement_assessments,
            "infeasible_product_codes": [
                code
                for code, assessment in procurement_assessments.items()
                if not assessment["feasible"]
            ],
        },
        "dispatch": {
            "method": {
                "id": "00000000-0000-4000-8000-000000000125",
                "key": "dispatch.minimum_carbon.consecutive_windows",
                "version": "1.0.0",
                "code_version": "carbonmesh-api-0.1.0",
            },
            **dispatch_identities,
            "load_code": "BATCH-PROCESS-7",
            "power_kw": "500",
            "duration_minutes": 120,
            "baseline_start": iso_z(dispatch.baseline.start),
            "baseline_end": iso_z(dispatch.baseline.end),
            "recommended_start": iso_z(dispatch.recommended.start),
            "recommended_end": iso_z(dispatch.recommended.end),
            "baseline_emissions_kgco2e": format(dispatch.baseline.emissions_kgco2e, "f"),
            "expected_emissions_kgco2e": format(dispatch.recommended.emissions_kgco2e, "f"),
            "avoided_kgco2e": format(dispatch.avoided_kgco2e, "f"),
            "reduction_pct": format(dispatch.reduction_pct, "f"),
            "actuation_authorized": False,
            "terminal_state": "approval_required",
        },
    }
    write_json(DEMO / "expected-results.json", expected)
    return expected


def build_api_e2e_expected(expected: dict[str, Any]) -> None:
    """Derive active HTTP-journey expectations from the golden service outputs."""

    purchased_material = expected["measurement"]["purchased_material"]
    procurement = expected["procurement"]
    recommended_pcf = Decimal(procurement["projected_footprint_kgco2e"]) / Decimal(
        procurement["quantity_kg"]
    )
    write_json(
        DEMO / "api_e2e_expected.json",
        {
            "synthetic": True,
            "company": expected["company"],
            "site": "Plant B",
            "reporting_period": expected["reporting_period"],
            "material_code": purchased_material["material_code"],
            "quantity_kg": purchased_material["quantity_kg"],
            "current_product_pcf_kgco2e_per_kg": purchased_material["factor_kgco2e_per_kg"],
            "measured_footprint_kgco2e": purchased_material["emissions_kgco2e"],
            "recommended_product_pcf_kgco2e_per_kg": format(recommended_pcf, "f"),
            "projected_footprint_kgco2e": procurement["projected_footprint_kgco2e"],
            "avoided_emissions_kgco2e": procurement["avoided_kgco2e"],
            "reduction_pct": procurement["reduction_pct"],
            "maximum_cost_increase_pct": procurement["maximum_cost_increase_pct"],
            "recommended_cost_delta_pct": procurement["cost_delta_pct"],
            "dispatch": {
                "baseline_start": expected["dispatch"]["baseline_start"],
                "baseline_end": expected["dispatch"]["baseline_end"],
                "recommended_start": expected["dispatch"]["recommended_start"],
                "recommended_end": expected["dispatch"]["recommended_end"],
                "baseline_emissions_kgco2e": expected["dispatch"]["baseline_emissions_kgco2e"],
                "expected_emissions_kgco2e": expected["dispatch"]["expected_emissions_kgco2e"],
                "avoided_kgco2e": expected["dispatch"]["avoided_kgco2e"],
                "reduction_pct": expected["dispatch"]["reduction_pct"],
            },
        },
    )


def build_manifest() -> None:
    artifacts = []
    for name in DEMO_ARTIFACT_NAMES:
        content = (DEMO / name).read_bytes()
        artifacts.append({"path": f"data/demo/{name}", "sha256": sha256_bytes(content)})
    write_json(
        DEMO / "manifest-v1.json",
        {
            "manifest_version": "1.0.0",
            "fixture_id": "maverick-q3-2026-v1",
            "synthetic": True,
            "generated_by": "scripts/generate_demo_fixtures.py",
            "artifacts": artifacts,
        },
    )


def main(output_directory: Path | None = None) -> None:
    global DEMO
    if output_directory is not None:
        DEMO = output_directory.resolve()
    DEMO.mkdir(parents=True, exist_ok=True)
    build_purchased_material_fixture()
    electricity_rows, scope2_total = build_electricity_fixture()
    build_history_fixture()
    build_forecast_fixtures()
    build_assurance_fixtures()
    expected = build_expected_results(electricity_rows, scope2_total)
    build_api_e2e_expected(expected)
    build_manifest()
    build_complete_quarter_fixtures()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path)
    arguments = parser.parse_args()
    main(arguments.output_directory)
