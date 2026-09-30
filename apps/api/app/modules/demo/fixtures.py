"""Stable, synthetic records used by the resettable CarbonMesh demo."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Final
from uuid import UUID

from sqlalchemy.orm import DeclarativeBase

from app.db.models.carbon import ActivityRecord, EmissionFactor, RawActivityRecord
from app.db.models.core import (
    Actor,
    Company,
    DataSource,
    EvidenceItem,
    ReportingPeriod,
    Site,
    SourceDocument,
)
from app.db.models.procurement import Supplier, SupplierProduct
from app.db.models.semantic import MethodDefinition, MetricDefinition, SemanticEntity


def _id(value: int) -> UUID:
    """Return an easy-to-recognize stable UUID reserved for synthetic fixtures."""
    return UUID(f"00000000-0000-4000-8000-{value:012d}")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fixture_bytes(filename: str, fallback: str) -> bytes:
    """Load the checked-in source artifact while retaining packaged-app fallback."""
    repository_fixture = Path(__file__).resolve().parents[5] / "data" / "demo" / filename
    try:
        return repository_fixture.read_bytes()
    except OSError:
        return fallback.encode("utf-8")


DEMO_COMPANY_ID: Final = _id(1)
DEMO_SITE_ID: Final = _id(2)
DEMO_PERIOD_ID: Final = _id(3)
DEMO_ANALYST_ID: Final = _id(4)
DEMO_APPROVER_ID: Final = _id(5)

ACTIVITY_METRIC_ID: Final = _id(101)
EMISSIONS_METRIC_ID: Final = _id(102)
PRODUCT_PCF_METRIC_ID: Final = _id(103)
CIRCULARITY_METRIC_ID: Final = _id(104)
AVOIDED_EMISSIONS_METRIC_ID: Final = _id(105)
COST_DELTA_METRIC_ID: Final = _id(106)
MEASUREMENT_METHOD_ID: Final = _id(121)
CONFIDENCE_METHOD_ID: Final = _id(122)
SCORING_METHOD_ID: Final = _id(123)

ACTIVITY_SOURCE_ID: Final = _id(201)
ACTIVITY_DOCUMENT_ID: Final = _id(202)
SUPPLIER_SOURCE_ID: Final = _id(203)
SUPPLIER_DOCUMENT_ID: Final = _id(204)
FACTOR_SOURCE_ID: Final = _id(205)
FACTOR_DOCUMENT_ID: Final = _id(206)
CURRENT_PRODUCT_EVIDENCE_ID: Final = _id(211)
ALTERNATIVE_A_EVIDENCE_ID: Final = _id(212)
ALTERNATIVE_B_EVIDENCE_ID: Final = _id(213)
FACTOR_EVIDENCE_ID: Final = _id(214)

CURRENT_SUPPLIER_ID: Final = _id(301)
ALTERNATIVE_A_SUPPLIER_ID: Final = _id(302)
ALTERNATIVE_B_SUPPLIER_ID: Final = _id(303)
CURRENT_PRODUCT_ID: Final = _id(311)
ALTERNATIVE_A_PRODUCT_ID: Final = _id(312)
ALTERNATIVE_B_PRODUCT_ID: Final = _id(313)
RAW_ACTIVITY_ID: Final = _id(401)
ACTIVITY_ID: Final = _id(402)
CURRENT_FACTOR_ID: Final = _id(403)


METRIC_FIXTURES: Final = (
    (
        ACTIVITY_METRIC_ID,
        "activity.purchased_material_mass",
        "Purchased material mass",
        "kg",
        {"company": True, "site": True, "period": True, "material": True},
        "measurement.activity",
        "1.0.0",
    ),
    (
        EMISSIONS_METRIC_ID,
        "emissions.scope3.category1",
        "Scope 3 Category 1 emissions",
        "kgCO2e",
        {"company": True, "site": True, "period": True, "material": True},
        "measurement.scope3_category1",
        "1.0.0",
    ),
    (
        PRODUCT_PCF_METRIC_ID,
        "supplier.product_carbon_footprint",
        "Supplier product carbon footprint",
        "kgCO2e/kg",
        {"supplier": True, "product": True, "effective_period": True},
        "procurement.product_pcf",
        "1.0.0",
    ),
    (
        CIRCULARITY_METRIC_ID,
        "supplier.circularity_score",
        "Supplier product circularity score",
        "score 0-100",
        {"supplier": True, "product": True},
        "procurement.circularity",
        "1.0.0",
    ),
    (
        AVOIDED_EMISSIONS_METRIC_ID,
        "procurement.projected_avoided_emissions",
        "Projected avoided emissions",
        "kgCO2e",
        {"scenario": True, "recommended_product": True},
        "procurement.impact",
        "1.0.0",
    ),
    (
        COST_DELTA_METRIC_ID,
        "procurement.cost_delta_pct",
        "Procurement cost delta",
        "percent",
        {"scenario": True, "recommended_product": True},
        "procurement.cost_delta",
        "1.0.0",
    ),
)


def build_demo_records() -> Sequence[DeclarativeBase]:
    """Build a fresh set of ORM objects for the documented synthetic POC."""
    company = Company(
        id=DEMO_COMPANY_ID,
        code="NOVA",
        name="Nova Components Ltd",
        is_synthetic=True,
        is_active=True,
    )
    site = Site(
        id=DEMO_SITE_ID,
        company_id=DEMO_COMPANY_ID,
        code="PLANT-B",
        name="Plant B",
        country_code="IN",
        timezone="Asia/Kolkata",
        is_active=True,
    )
    period = ReportingPeriod(
        id=DEMO_PERIOD_ID,
        company_id=DEMO_COMPANY_ID,
        name="Q3 2026",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 9, 30),
        status="closed",
    )
    actors = (
        Actor(
            id=DEMO_ANALYST_ID,
            company_id=DEMO_COMPANY_ID,
            email="analyst@nova.example.invalid",
            display_name="Demo Sustainability Analyst",
            role="sustainability_analyst",
            is_active=True,
        ),
        Actor(
            id=DEMO_APPROVER_ID,
            company_id=DEMO_COMPANY_ID,
            email="approver@nova.example.invalid",
            display_name="Demo Procurement Approver",
            role="approver",
            is_active=True,
        ),
    )

    semantic_entities = tuple(
        SemanticEntity(
            id=_id(141 + index),
            company_id=DEMO_COMPANY_ID,
            entity_type="metric",
            key=key,
            label=name,
            description=f"Synthetic POC definition for {name.lower()}.",
            entity_metadata={"synthetic": True},
            is_active=True,
        )
        for index, (_, key, name, *_rest) in enumerate(METRIC_FIXTURES)
    )
    metrics = tuple(
        MetricDefinition(
            id=metric_id,
            company_id=DEMO_COMPANY_ID,
            semantic_entity_id=semantic_entities[index].id,
            key=key,
            version="1.0.0",
            name=name,
            canonical_unit=unit,
            dimensions=dimensions,
            handler=handler,
            method_version=method_version,
            description=f"Synthetic CarbonMesh POC metric: {name}.",
            is_active=True,
        )
        for index, (
            metric_id,
            key,
            name,
            unit,
            dimensions,
            handler,
            method_version,
        ) in enumerate(METRIC_FIXTURES)
    )
    methods = (
        MethodDefinition(
            id=MEASUREMENT_METHOD_ID,
            company_id=DEMO_COMPANY_ID,
            method_type="measurement",
            key="measurement.scope3.category1.mass_factor",
            version="1.0.0",
            name="Purchased material mass-factor calculation",
            code_version="carbonmesh-api-0.1.0",
            configuration={
                "formula": "normalized_quantity * emission_factor",
                "rounding_policy": "ROUND_HALF_EVEN",
                "output_scale": 6,
                "confidence_weights": {
                    "source_quality": "0.40",
                    "factor_specificity": "0.30",
                    "factor_recency": "0.20",
                    "record_completeness": "0.10",
                },
            },
            effective_from=date(2026, 1, 1),
            is_active=True,
        ),
        MethodDefinition(
            id=CONFIDENCE_METHOD_ID,
            company_id=DEMO_COMPANY_ID,
            method_type="confidence",
            key="measurement.confidence.weighted",
            version="1.0.0",
            name="Measurement confidence weighting",
            code_version="carbonmesh-api-0.1.0",
            configuration={
                "source_quality": "0.40",
                "factor_specificity": "0.30",
                "factor_recency": "0.20",
                "record_completeness": "0.10",
            },
            effective_from=date(2026, 1, 1),
            is_active=True,
        ),
        MethodDefinition(
            id=SCORING_METHOD_ID,
            company_id=DEMO_COMPANY_ID,
            method_type="supplier_scoring",
            key="procurement.supplier_scoring",
            version="1.0.0",
            name="CarbonMesh supplier scoring",
            code_version="carbonmesh-api-0.1.0",
            configuration={
                "carbon": "0.40",
                "evidence": "0.25",
                "circularity": "0.20",
                "operational_fit": "0.15",
            },
            effective_from=date(2026, 1, 1),
            is_active=True,
        ),
    )

    activity_csv = (
        "row_key,material_code,quantity,unit,activity_date,supplier_code,"
        "product_code,unit_cost,currency,synthetic\n"
        "plant-b-packaging-q3,PACKAGING-TRAY,12000,kg,2026-09-15,"
        "CURRENT-SUPPLIER,TRAY-CURRENT,1.000000,USD,true\n"
    )
    supplier_evidence_text = (
        "Synthetic supplier product declarations for Nova Components Ltd. "
        "Values are demo-only and are not third-party verified."
    )
    factor_evidence_text = (
        "Synthetic product-specific cradle-to-gate factor: TRAY-CURRENT = "
        "2.8 kgCO2e/kg, effective 2026-01-01 for India."
    )
    activity_document = _fixture_bytes("activity.csv", activity_csv)
    supplier_document = _fixture_bytes("suppliers.json", supplier_evidence_text)
    factor_document = _fixture_bytes("factor-evidence.txt", factor_evidence_text)
    factor_evidence_content = factor_document.decode("utf-8")
    sources = (
        DataSource(
            id=ACTIVITY_SOURCE_ID,
            company_id=DEMO_COMPANY_ID,
            site_id=DEMO_SITE_ID,
            name="Synthetic Plant B activity import",
            source_type="synthetic",
            status="ready",
            external_reference="data/demo/activity.csv",
            configuration={
                "import_type": "activity",
                "import_status": "completed",
                "source_name": "Synthetic Plant B activity import",
                "filename": "activity.csv",
                "document_checksum": hashlib.sha256(activity_document).hexdigest(),
                "source_document_id": str(ACTIVITY_DOCUMENT_ID),
                "accepted_count": 1,
                "rejected_count": 0,
                "issue_count": 0,
                "synthetic": True,
            },
            is_synthetic=True,
        ),
        DataSource(
            id=SUPPLIER_SOURCE_ID,
            company_id=DEMO_COMPANY_ID,
            name="Synthetic supplier product import",
            source_type="synthetic",
            status="ready",
            external_reference="data/demo/suppliers.json",
            configuration={
                "import_type": "suppliers",
                "import_status": "completed",
                "source_name": "Synthetic supplier product import",
                "filename": "suppliers.json",
                "document_checksum": hashlib.sha256(supplier_document).hexdigest(),
                "source_document_id": str(SUPPLIER_DOCUMENT_ID),
                "accepted_count": 3,
                "rejected_count": 0,
                "issue_count": 0,
                "synthetic": True,
            },
            is_synthetic=True,
        ),
        DataSource(
            id=FACTOR_SOURCE_ID,
            company_id=DEMO_COMPANY_ID,
            site_id=DEMO_SITE_ID,
            name="Synthetic emissions factor evidence",
            source_type="synthetic",
            status="ready",
            external_reference="data/demo/factor-evidence.txt",
            configuration={"import_kind": "factor", "synthetic": True},
            is_synthetic=True,
        ),
    )
    documents = (
        SourceDocument(
            id=ACTIVITY_DOCUMENT_ID,
            company_id=DEMO_COMPANY_ID,
            data_source_id=ACTIVITY_SOURCE_ID,
            filename="activity.csv",
            content_type="text/csv",
            checksum=hashlib.sha256(activity_document).hexdigest(),
            size_bytes=len(activity_document),
            storage_uri="fixture://data/demo/activity.csv",
            document_metadata={"synthetic": True, "reporting_period": "Q3 2026"},
        ),
        SourceDocument(
            id=SUPPLIER_DOCUMENT_ID,
            company_id=DEMO_COMPANY_ID,
            data_source_id=SUPPLIER_SOURCE_ID,
            filename="suppliers.json",
            content_type="application/json",
            checksum=hashlib.sha256(supplier_document).hexdigest(),
            size_bytes=len(supplier_document),
            storage_uri="fixture://data/demo/suppliers.json",
            document_metadata={"synthetic": True},
        ),
        SourceDocument(
            id=FACTOR_DOCUMENT_ID,
            company_id=DEMO_COMPANY_ID,
            data_source_id=FACTOR_SOURCE_ID,
            filename="factor-evidence.txt",
            content_type="text/plain",
            checksum=hashlib.sha256(factor_document).hexdigest(),
            size_bytes=len(factor_document),
            storage_uri="fixture://data/demo/factor-evidence.txt",
            document_metadata={"synthetic": True},
        ),
    )
    evidence = (
        EvidenceItem(
            id=CURRENT_PRODUCT_EVIDENCE_ID,
            company_id=DEMO_COMPANY_ID,
            source_document_id=SUPPLIER_DOCUMENT_ID,
            evidence_type="supplier_product_declaration",
            locator="products[TRAY-CURRENT]",
            content_text="Synthetic PCF 2.8 kgCO2e/kg for the current packaging tray.",
            checksum=_sha256(
                "Synthetic PCF 2.8 kgCO2e/kg for the current packaging tray."
            ),
            evidence_metadata={"synthetic": True, "product_code": "TRAY-CURRENT"},
        ),
        EvidenceItem(
            id=ALTERNATIVE_A_EVIDENCE_ID,
            company_id=DEMO_COMPANY_ID,
            source_document_id=SUPPLIER_DOCUMENT_ID,
            evidence_type="supplier_product_declaration",
            locator="products[TRAY-ALT-A]",
            content_text="Synthetic PCF 2.2 kgCO2e/kg for packaging tray alternative A.",
            checksum=_sha256(
                "Synthetic PCF 2.2 kgCO2e/kg for packaging tray alternative A."
            ),
            evidence_metadata={"synthetic": True, "product_code": "TRAY-ALT-A"},
        ),
        EvidenceItem(
            id=ALTERNATIVE_B_EVIDENCE_ID,
            company_id=DEMO_COMPANY_ID,
            source_document_id=SUPPLIER_DOCUMENT_ID,
            evidence_type="supplier_product_declaration",
            locator="products[TRAY-ALT-B]",
            content_text="Synthetic PCF 1.9 kgCO2e/kg for packaging tray alternative B.",
            checksum=_sha256(
                "Synthetic PCF 1.9 kgCO2e/kg for packaging tray alternative B."
            ),
            evidence_metadata={"synthetic": True, "product_code": "TRAY-ALT-B"},
        ),
        EvidenceItem(
            id=FACTOR_EVIDENCE_ID,
            company_id=DEMO_COMPANY_ID,
            source_document_id=FACTOR_DOCUMENT_ID,
            evidence_type="emission_factor",
            locator="factor:TRAY-CURRENT-PCF:v1.0.0",
            content_text=factor_evidence_content,
            checksum=hashlib.sha256(factor_document).hexdigest(),
            evidence_metadata={"synthetic": True, "geography": "IN"},
        ),
    )

    suppliers = (
        Supplier(
            id=CURRENT_SUPPLIER_ID,
            company_id=DEMO_COMPANY_ID,
            supplier_code="CURRENT-SUPPLIER",
            name="Current Packaging Co (Synthetic)",
            country_code="IN",
            status="active",
            supplier_metadata={"synthetic": True},
        ),
        Supplier(
            id=ALTERNATIVE_A_SUPPLIER_ID,
            company_id=DEMO_COMPANY_ID,
            supplier_code="ALTERNATIVE-A",
            name="Circular Trays A (Synthetic)",
            country_code="IN",
            status="active",
            supplier_metadata={"synthetic": True},
        ),
        Supplier(
            id=ALTERNATIVE_B_SUPPLIER_ID,
            company_id=DEMO_COMPANY_ID,
            supplier_code="ALTERNATIVE-B",
            name="Low Carbon Trays B (Synthetic)",
            country_code="IN",
            status="active",
            supplier_metadata={"synthetic": True},
        ),
    )
    product_common = {
        "company_id": DEMO_COMPANY_ID,
        "material_code": "PACKAGING-TRAY",
        "category": "packaging",
        "pcf_unit": "kgCO2e/kg",
        "currency": "USD",
        "effective_from": date(2026, 1, 1),
        "is_active": True,
    }
    products = (
        SupplierProduct(
            id=CURRENT_PRODUCT_ID,
            supplier_id=CURRENT_SUPPLIER_ID,
            evidence_item_id=CURRENT_PRODUCT_EVIDENCE_ID,
            product_code="TRAY-CURRENT",
            name="Current packaging tray",
            description="Synthetic current product for the Plant B demo.",
            pcf_kgco2e_per_unit=Decimal("2.8"),
            circularity_score=Decimal(45),
            recycled_content_pct=Decimal(30),
            recyclable_pct=Decimal(75),
            evidence_quality_score=Decimal(90),
            lead_time_days=12,
            unit_cost=Decimal("1.000000"),
            **product_common,
        ),
        SupplierProduct(
            id=ALTERNATIVE_A_PRODUCT_ID,
            supplier_id=ALTERNATIVE_A_SUPPLIER_ID,
            evidence_item_id=ALTERNATIVE_A_EVIDENCE_ID,
            product_code="TRAY-ALT-A",
            name="Packaging tray alternative A",
            description="Synthetic lower-carbon comparison product.",
            pcf_kgco2e_per_unit=Decimal("2.2"),
            circularity_score=Decimal(68),
            recycled_content_pct=Decimal(60),
            recyclable_pct=Decimal(90),
            evidence_quality_score=Decimal(86),
            lead_time_days=10,
            unit_cost=Decimal("0.980000"),
            **product_common,
        ),
        SupplierProduct(
            id=ALTERNATIVE_B_PRODUCT_ID,
            supplier_id=ALTERNATIVE_B_SUPPLIER_ID,
            evidence_item_id=ALTERNATIVE_B_EVIDENCE_ID,
            product_code="TRAY-ALT-B",
            name="Packaging tray alternative B",
            description="Synthetic recommended lower-carbon comparison product.",
            pcf_kgco2e_per_unit=Decimal("1.9"),
            circularity_score=Decimal(78),
            recycled_content_pct=Decimal(75),
            recyclable_pct=Decimal(95),
            evidence_quality_score=Decimal(92),
            lead_time_days=14,
            unit_cost=Decimal("1.032000"),
            **product_common,
        ),
    )

    raw_payload = {
        "row_key": "plant-b-packaging-q3",
        "material_code": "PACKAGING-TRAY",
        "quantity": "12000",
        "unit": "kg",
        "activity_date": "2026-09-15",
        "supplier_code": "CURRENT-SUPPLIER",
        "product_code": "TRAY-CURRENT",
        "unit_cost": "1.000000",
        "currency": "USD",
        "synthetic": "true",
    }
    raw_activity = RawActivityRecord(
        id=RAW_ACTIVITY_ID,
        company_id=DEMO_COMPANY_ID,
        data_source_id=ACTIVITY_SOURCE_ID,
        source_document_id=ACTIVITY_DOCUMENT_ID,
        row_key="plant-b-packaging-q3",
        row_number=2,
        raw_payload=raw_payload,
        checksum=_sha256(json.dumps(raw_payload, sort_keys=True, separators=(",", ":"))),
        import_status="accepted",
    )
    activity = ActivityRecord(
        id=ACTIVITY_ID,
        company_id=DEMO_COMPANY_ID,
        raw_activity_record_id=RAW_ACTIVITY_ID,
        site_id=DEMO_SITE_ID,
        reporting_period_id=DEMO_PERIOD_ID,
        metric_definition_id=ACTIVITY_METRIC_ID,
        supplier_product_id=CURRENT_PRODUCT_ID,
        material_code="PACKAGING-TRAY",
        activity_date=date(2026, 9, 15),
        quantity=Decimal(12000),
        unit="kg",
        normalized_quantity=Decimal(12000),
        normalized_unit="kg",
        unit_cost=Decimal("1.000000"),
        currency="USD",
        status="valid",
    )
    factor = EmissionFactor(
        id=CURRENT_FACTOR_ID,
        company_id=DEMO_COMPANY_ID,
        metric_definition_id=EMISSIONS_METRIC_ID,
        evidence_item_id=FACTOR_EVIDENCE_ID,
        factor_code="TRAY-CURRENT-PCF",
        version="1.0.0",
        name="Synthetic current tray product carbon footprint",
        material_code="PACKAGING-TRAY",
        product_code="TRAY-CURRENT",
        geography="IN",
        factor_value=Decimal("2.8"),
        numerator_unit="kgCO2e",
        denominator_unit="kg",
        effective_from=date(2026, 1, 1),
        source_quality=Decimal("0.95"),
        factor_specificity=Decimal("1.00"),
        factor_recency=Decimal("1.00"),
        status="active",
    )

    return (
        company,
        site,
        period,
        *actors,
        *semantic_entities,
        *metrics,
        *methods,
        *sources,
        *documents,
        *evidence,
        *suppliers,
        *products,
        raw_activity,
        activity,
        factor,
    )
