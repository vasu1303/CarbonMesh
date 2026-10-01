"""Stable, synthetic records used by the resettable CarbonMesh demo."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final
from uuid import UUID

from sqlalchemy.orm import DeclarativeBase

from app.db.models.assurance import DisclosureRequirement, Standard
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
from app.db.models.dispatch import FlexibleLoad, OperatingConstraint
from app.db.models.procurement import Supplier, SupplierProduct
from app.db.models.semantic import (
    MethodDefinition,
    MetricDefinition,
    PolicyDefinition,
    SemanticEntity,
)


def _id(value: int) -> UUID:
    """Return an easy-to-recognize stable UUID reserved for synthetic fixtures."""
    return UUID(f"00000000-0000-4000-8000-{value:012d}")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


RUNTIME_FIXTURE_NAMES: Final = (
    "activity.csv",
    "suppliers.json",
    "factor-evidence.txt",
    "electricity-hourly.csv",
    "assurance-standard.json",
    "assurance-evidence.md",
)
FIXTURE_ID: Final = "maverick-q3-2026-v1"
MANIFEST_VERSION: Final = "1.0.0"


def _fixture_directory() -> Path:
    """Resolve one authoritative fixture bundle without mixing directories."""
    module_path = Path(__file__).resolve()
    for ancestor in module_path.parents:
        repository_fixture = ancestor / "data" / "demo"
        if repository_fixture.is_dir():
            return repository_fixture
    bundled_fixture = module_path.parents[2] / "demo_fixtures"
    if bundled_fixture.is_dir():
        return bundled_fixture
    raise RuntimeError("The required Maverick demo fixture bundle is unavailable.")


def _load_fixture_bundle(directory: Path) -> dict[str, bytes]:
    """Read and hash-validate every runtime artifact declared by the manifest."""
    manifest_path = directory / "manifest-v1.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"The Maverick demo fixture manifest is missing or invalid: {manifest_path}."
        ) from error

    if (
        not isinstance(manifest, dict)
        or manifest.get("manifest_version") != MANIFEST_VERSION
        or manifest.get("fixture_id") != FIXTURE_ID
        or manifest.get("synthetic") is not True
        or not isinstance(manifest.get("artifacts"), list)
    ):
        raise RuntimeError("The Maverick demo fixture manifest contract is invalid.")

    hashes: dict[str, str] = {}
    for artifact in manifest["artifacts"]:
        if not isinstance(artifact, dict):
            raise TypeError("The Maverick demo fixture manifest contains an invalid artifact.")
        path = artifact.get("path")
        digest = artifact.get("sha256")
        if (
            not isinstance(path, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or path in hashes
        ):
            raise RuntimeError("The Maverick demo fixture manifest contains an invalid artifact.")
        hashes[path] = digest

    contents: dict[str, bytes] = {}
    for filename in RUNTIME_FIXTURE_NAMES:
        manifest_key = f"data/demo/{filename}"
        expected_digest = hashes.get(manifest_key)
        if expected_digest is None:
            raise RuntimeError(f"The Maverick demo fixture manifest does not declare {filename}.")
        artifact_path = directory / filename
        try:
            content = artifact_path.read_bytes()
        except OSError as error:
            raise RuntimeError(
                f"The required Maverick demo fixture is unavailable: {artifact_path}."
            ) from error
        if not content:
            raise RuntimeError(f"The required Maverick demo fixture is empty: {artifact_path}.")
        actual_digest = hashlib.sha256(content).hexdigest()
        if actual_digest != expected_digest:
            raise RuntimeError(
                f"The Maverick demo fixture failed integrity validation: {artifact_path}."
            )
        contents[filename] = content
    return contents


DEMO_COMPANY_ID: Final = _id(1)
DEMO_SITE_ID: Final = _id(2)
DEMO_PERIOD_ID: Final = _id(3)
DEMO_ANALYST_ID: Final = _id(4)
DEMO_APPROVER_ID: Final = _id(5)
DEMO_PROCUREMENT_MANAGER_ID: Final = _id(6)

ACTIVITY_METRIC_ID: Final = _id(101)
EMISSIONS_METRIC_ID: Final = _id(102)
PRODUCT_PCF_METRIC_ID: Final = _id(103)
CIRCULARITY_METRIC_ID: Final = _id(104)
AVOIDED_EMISSIONS_METRIC_ID: Final = _id(105)
COST_DELTA_METRIC_ID: Final = _id(106)
ELECTRICITY_ACTIVITY_METRIC_ID: Final = _id(107)
SCOPE2_METRIC_ID: Final = _id(108)
GRID_INTENSITY_METRIC_ID: Final = _id(109)
MEASUREMENT_METHOD_ID: Final = _id(121)
CONFIDENCE_METHOD_ID: Final = _id(122)
SCORING_METHOD_ID: Final = _id(123)
SCOPE2_METHOD_ID: Final = _id(124)
DISPATCH_METHOD_ID: Final = _id(125)
DISPATCH_POLICY_ID: Final = _id(126)

ACTIVITY_SOURCE_ID: Final = _id(201)
ACTIVITY_DOCUMENT_ID: Final = _id(202)
SUPPLIER_SOURCE_ID: Final = _id(203)
SUPPLIER_DOCUMENT_ID: Final = _id(204)
FACTOR_SOURCE_ID: Final = _id(205)
FACTOR_DOCUMENT_ID: Final = _id(206)
ELECTRICITY_SOURCE_ID: Final = _id(207)
ELECTRICITY_DOCUMENT_ID: Final = _id(208)
ASSURANCE_SOURCE_ID: Final = _id(209)
ASSURANCE_STANDARD_DOCUMENT_ID: Final = _id(210)
CURRENT_PRODUCT_EVIDENCE_ID: Final = _id(211)
ALTERNATIVE_A_EVIDENCE_ID: Final = _id(212)
ALTERNATIVE_B_EVIDENCE_ID: Final = _id(213)
FACTOR_EVIDENCE_ID: Final = _id(214)
ALTERNATIVE_C_EVIDENCE_ID: Final = _id(215)
ASSURANCE_EVIDENCE_DOCUMENT_ID: Final = _id(216)
ASSURANCE_EVIDENCE_ID: Final = _id(217)

CURRENT_SUPPLIER_ID: Final = _id(301)
ALTERNATIVE_A_SUPPLIER_ID: Final = _id(302)
ALTERNATIVE_B_SUPPLIER_ID: Final = _id(303)
ALTERNATIVE_C_SUPPLIER_ID: Final = _id(304)
CURRENT_PRODUCT_ID: Final = _id(311)
ALTERNATIVE_A_PRODUCT_ID: Final = _id(312)
ALTERNATIVE_B_PRODUCT_ID: Final = _id(313)
ALTERNATIVE_C_PRODUCT_ID: Final = _id(314)
RAW_ACTIVITY_ID: Final = _id(401)
ACTIVITY_ID: Final = _id(402)
CURRENT_FACTOR_ID: Final = _id(403)
FLEXIBLE_LOAD_ID: Final = _id(501)
AVAILABILITY_CONSTRAINT_ID: Final = _id(502)
DEADLINE_CONSTRAINT_ID: Final = _id(503)
BLACKOUT_CONSTRAINT_ID: Final = _id(504)
POWER_CONSTRAINT_ID: Final = _id(505)
ASSURANCE_STANDARD_ID: Final = _id(601)
ASSURANCE_BOUNDARY_REQUIREMENT_ID: Final = _id(602)
ASSURANCE_TOTAL_REQUIREMENT_ID: Final = _id(603)
ASSURANCE_REDUCTION_REQUIREMENT_ID: Final = _id(604)


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
        ELECTRICITY_ACTIVITY_METRIC_ID,
        "activity.electricity_consumption",
        "Hourly electricity consumption",
        "kWh",
        {"company": True, "site": True, "period": True, "time": "hourly"},
        "measurement.electricity_activity",
        "1.0.0",
    ),
    (
        SCOPE2_METRIC_ID,
        "emissions.scope2.location_based",
        "Location-based Scope 2 emissions",
        "kgCO2e",
        {"company": True, "site": True, "period": True, "time": "hourly"},
        "measurement.scope2_location_based",
        "scope2-hourly-v1",
    ),
    (
        GRID_INTENSITY_METRIC_ID,
        "electricity.grid_carbon_intensity",
        "Electricity grid carbon intensity",
        "gCO2e/kWh",
        {"site": True, "geography": "electricity_maps_zone", "time": "hourly"},
        "integrations.electricity_maps",
        "electricity-maps-v4",
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
        code="MAVERICK",
        name="Maverick Manufacturing (synthetic)",
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
        electricity_maps_zone="IN",
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
            email="analyst@maverick.example.invalid",
            display_name="Demo Sustainability Analyst",
            role="sustainability_analyst",
            is_active=True,
        ),
        Actor(
            id=DEMO_APPROVER_ID,
            company_id=DEMO_COMPANY_ID,
            email="approver@maverick.example.invalid",
            display_name="Demo Procurement Approver",
            role="approver",
            is_active=True,
        ),
        Actor(
            id=DEMO_PROCUREMENT_MANAGER_ID,
            company_id=DEMO_COMPANY_ID,
            email="procurement@maverick.example.invalid",
            display_name="Demo Procurement Manager",
            role="procurement_manager",
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
            version="2.0.0" if key == "electricity.grid_carbon_intensity" else "1.0.0",
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
            version="2.0.0",
            name="Purchased material mass-factor calculation",
            code_version="carbonmesh-api-0.1.0",
            configuration={
                "formula": "normalized_quantity * emission_factor",
                "rounding_policy": "ROUND_HALF_EVEN",
                "output_scale": 6,
                "confidence_method": "measurement-confidence-v2",
            },
            effective_from=date(2026, 1, 1),
            is_active=True,
        ),
        MethodDefinition(
            id=CONFIDENCE_METHOD_ID,
            company_id=DEMO_COMPANY_ID,
            method_type="confidence",
            key="measurement.confidence.weighted",
            version="2.0.0",
            name="Measurement confidence weighting v2",
            code_version="carbonmesh-api-0.1.0",
            configuration={
                "source_quality": "0.35",
                "method_fit": "0.25",
                "temporal_match": "0.20",
                "completeness": "0.20",
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
        MethodDefinition(
            id=SCOPE2_METHOD_ID,
            company_id=DEMO_COMPANY_ID,
            method_type="measurement",
            key="measurement.scope2.location_based.hourly",
            version="1.0.0",
            name="Hourly location-based Scope 2 calculation",
            code_version="carbonmesh-api-0.1.0",
            configuration={
                "formula": "kWh * gCO2e_per_kWh / 1000",
                "missing_interval_policy": "fail_closed",
                "rounding_policy": "ROUND_HALF_UP",
                "output_scale": 6,
            },
            effective_from=date(2026, 1, 1),
            is_active=True,
        ),
        MethodDefinition(
            id=DISPATCH_METHOD_ID,
            company_id=DEMO_COMPANY_ID,
            method_type="dispatch_optimization",
            key="dispatch.minimum_carbon.consecutive_windows",
            version="1.0.0",
            name="Deterministic advisory dispatch optimizer",
            code_version="carbonmesh-api-0.1.0",
            configuration={
                "objective": "minimum_carbon",
                "tie_break": "earliest_feasible_start",
                "missing_interval_policy": "invalidate_candidate",
                "actuation_authorized": False,
            },
            effective_from=date(2026, 1, 1),
            is_active=True,
        ),
    )
    policies = (
        PolicyDefinition(
            id=DISPATCH_POLICY_ID,
            company_id=DEMO_COMPANY_ID,
            policy_type="dispatch",
            key="dispatch.advisory_only",
            version="1.0.0",
            name="Advisory-only dispatch policy",
            description="Synthetic demo policy that prohibits equipment actuation.",
            configuration={"actuation_authorized": False, "approval_required": True},
            effective_from=date(2026, 1, 1),
            is_active=True,
        ),
    )

    fixture_bundle = _load_fixture_bundle(_fixture_directory())
    activity_document = fixture_bundle["activity.csv"]
    supplier_document = fixture_bundle["suppliers.json"]
    factor_document = fixture_bundle["factor-evidence.txt"]
    electricity_document = fixture_bundle["electricity-hourly.csv"]
    assurance_standard_document = fixture_bundle["assurance-standard.json"]
    assurance_evidence_document = fixture_bundle["assurance-evidence.md"]
    try:
        supplier_payload = json.loads(supplier_document.decode("utf-8"))
        supplier_products = supplier_payload["products"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("The Maverick supplier fixture contract is invalid.") from error
    if (
        not isinstance(supplier_payload, dict)
        or supplier_payload.get("synthetic") is not True
        or supplier_payload.get("fixture_version") != FIXTURE_ID
        or not isinstance(supplier_products, list)
        or not supplier_products
    ):
        raise RuntimeError("The Maverick supplier fixture contract is invalid.")
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
                "accepted_count": len(supplier_products),
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
        DataSource(
            id=ELECTRICITY_SOURCE_ID,
            company_id=DEMO_COMPANY_ID,
            site_id=DEMO_SITE_ID,
            name="Synthetic Plant B hourly electricity import",
            source_type="synthetic",
            status="ready",
            external_reference="data/demo/electricity-hourly.csv",
            configuration={
                "import_type": "hourly_electricity",
                "fixture_version": "maverick-q3-2026-v1",
                "expected_intervals": 2160,
                "input_rows": 2160,
                "declared_quality_cases": {
                    "missing_interval": 1,
                    "duplicate_timestamp": 1,
                },
                "electricity_maps_zone": "IN",
                "synthetic": True,
            },
            is_synthetic=True,
        ),
        DataSource(
            id=ASSURANCE_SOURCE_ID,
            company_id=DEMO_COMPANY_ID,
            site_id=DEMO_SITE_ID,
            name="Synthetic Scope 2 assurance evidence",
            source_type="synthetic",
            status="ready",
            external_reference="data/demo/assurance-evidence.md",
            configuration={
                "fixture_version": "maverick-q3-2026-v1",
                "poc_draft": True,
                "not_an_assurance_opinion": True,
                "synthetic": True,
            },
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
        SourceDocument(
            id=ELECTRICITY_DOCUMENT_ID,
            company_id=DEMO_COMPANY_ID,
            data_source_id=ELECTRICITY_SOURCE_ID,
            filename="electricity-hourly.csv",
            content_type="text/csv",
            checksum=hashlib.sha256(electricity_document).hexdigest(),
            size_bytes=len(electricity_document),
            storage_uri="fixture://data/demo/electricity-hourly.csv",
            document_metadata={
                "synthetic": True,
                "fixture_version": "maverick-q3-2026-v1",
                "reporting_period": "Q3 2026",
                "hour_count": 2160,
            },
        ),
        SourceDocument(
            id=ASSURANCE_STANDARD_DOCUMENT_ID,
            company_id=DEMO_COMPANY_ID,
            data_source_id=ASSURANCE_SOURCE_ID,
            filename="assurance-standard.json",
            content_type="application/json",
            checksum=hashlib.sha256(assurance_standard_document).hexdigest(),
            size_bytes=len(assurance_standard_document),
            storage_uri="fixture://data/demo/assurance-standard.json",
            document_metadata={"synthetic": True, "poc_draft": True},
        ),
        SourceDocument(
            id=ASSURANCE_EVIDENCE_DOCUMENT_ID,
            company_id=DEMO_COMPANY_ID,
            data_source_id=ASSURANCE_SOURCE_ID,
            filename="assurance-evidence.md",
            content_type="text/markdown",
            checksum=hashlib.sha256(assurance_evidence_document).hexdigest(),
            size_bytes=len(assurance_evidence_document),
            storage_uri="fixture://data/demo/assurance-evidence.md",
            document_metadata={
                "synthetic": True,
                "poc_draft": True,
                "not_an_assurance_opinion": True,
            },
        ),
    )
    evidence = (
        EvidenceItem(
            id=CURRENT_PRODUCT_EVIDENCE_ID,
            company_id=DEMO_COMPANY_ID,
            source_document_id=SUPPLIER_DOCUMENT_ID,
            evidence_type="supplier_product_declaration",
            locator="products[AL-CURRENT]",
            content_text="Synthetic product PCF: 8.6 kgCO2e/kg; recycled content: 60%.",
            checksum=_sha256("Synthetic product PCF: 8.6 kgCO2e/kg; recycled content: 60%."),
            evidence_metadata={"synthetic": True, "product_code": "AL-CURRENT"},
        ),
        EvidenceItem(
            id=ALTERNATIVE_A_EVIDENCE_ID,
            company_id=DEMO_COMPANY_ID,
            source_document_id=SUPPLIER_DOCUMENT_ID,
            evidence_type="supplier_product_declaration",
            locator="products[AL-RECYCLED-A]",
            content_text="Synthetic product PCF: 4.2 kgCO2e/kg; recycled content: 82%.",
            checksum=_sha256("Synthetic product PCF: 4.2 kgCO2e/kg; recycled content: 82%."),
            evidence_metadata={"synthetic": True, "product_code": "AL-RECYCLED-A"},
        ),
        EvidenceItem(
            id=ALTERNATIVE_B_EVIDENCE_ID,
            company_id=DEMO_COMPANY_ID,
            source_document_id=SUPPLIER_DOCUMENT_ID,
            evidence_type="supplier_product_declaration",
            locator="products[AL-RECYCLED-B]",
            content_text="Synthetic product PCF: 2.4 kgCO2e/kg; recycled content: 95%.",
            checksum=_sha256("Synthetic product PCF: 2.4 kgCO2e/kg; recycled content: 95%."),
            evidence_metadata={"synthetic": True, "product_code": "AL-RECYCLED-B"},
        ),
        EvidenceItem(
            id=ALTERNATIVE_C_EVIDENCE_ID,
            company_id=DEMO_COMPANY_ID,
            source_document_id=SUPPLIER_DOCUMENT_ID,
            evidence_type="supplier_product_declaration",
            locator="products[AL-RECYCLED-C]",
            content_text=("Synthetic product PCF: 1.9 kgCO2e/kg; cost exceeds the demo ceiling."),
            checksum=_sha256(
                "Synthetic product PCF: 1.9 kgCO2e/kg; cost exceeds the demo ceiling."
            ),
            evidence_metadata={"synthetic": True, "product_code": "AL-RECYCLED-C"},
        ),
        EvidenceItem(
            id=FACTOR_EVIDENCE_ID,
            company_id=DEMO_COMPANY_ID,
            source_document_id=FACTOR_DOCUMENT_ID,
            evidence_type="emission_factor",
            locator="factor:AL-CURRENT-PCF:initial",
            content_text=factor_evidence_content,
            checksum=hashlib.sha256(factor_document).hexdigest(),
            evidence_metadata={"synthetic": True, "geography": "IN"},
        ),
        EvidenceItem(
            id=ASSURANCE_EVIDENCE_ID,
            company_id=DEMO_COMPANY_ID,
            source_document_id=ASSURANCE_EVIDENCE_DOCUMENT_ID,
            evidence_type="disclosure_support",
            locator="maverick-scope2-boundary:q3-2026",
            content_text=assurance_evidence_document.decode("utf-8"),
            checksum=hashlib.sha256(assurance_evidence_document).hexdigest(),
            evidence_metadata={
                "synthetic": True,
                "company": "Maverick Manufacturing (synthetic)",
                "site": "Plant B (synthetic)",
                "reporting_period": "Q3 2026",
                "requirement_codes": ["S2-BOUNDARY", "S2-TOTAL"],
            },
        ),
    )

    suppliers = (
        Supplier(
            id=CURRENT_SUPPLIER_ID,
            company_id=DEMO_COMPANY_ID,
            supplier_code="MAVERICK-CURRENT",
            name="Maverick Metals Baseline (Synthetic)",
            country_code="IN",
            status="active",
            supplier_metadata={"synthetic": True, "risk": "low"},
        ),
        Supplier(
            id=ALTERNATIVE_A_SUPPLIER_ID,
            company_id=DEMO_COMPANY_ID,
            supplier_code="CIRCULAR-AL-A",
            name="Circular Aluminium A (Synthetic)",
            country_code="IN",
            status="active",
            supplier_metadata={"synthetic": True, "risk": "low"},
        ),
        Supplier(
            id=ALTERNATIVE_B_SUPPLIER_ID,
            company_id=DEMO_COMPANY_ID,
            supplier_code="CIRCULAR-AL-B",
            name="Circular Aluminium B (Synthetic)",
            country_code="IN",
            status="active",
            supplier_metadata={"synthetic": True, "risk": "low"},
        ),
        Supplier(
            id=ALTERNATIVE_C_SUPPLIER_ID,
            company_id=DEMO_COMPANY_ID,
            supplier_code="CIRCULAR-AL-C",
            name="Premium Circular Aluminium C (Synthetic)",
            country_code="IN",
            status="active",
            supplier_metadata={"synthetic": True, "risk": "medium"},
        ),
    )
    product_common = {
        "company_id": DEMO_COMPANY_ID,
        "material_code": "RECYCLED-ALUMINIUM",
        "category": "metals",
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
            product_code="AL-CURRENT",
            name="Current recycled aluminium billet",
            description="Synthetic current product for the Plant B demo.",
            pcf_kgco2e_per_unit=Decimal("8.6"),
            circularity_score=Decimal(62),
            recycled_content_pct=Decimal(60),
            recyclable_pct=Decimal(98),
            evidence_quality_score=Decimal(94),
            lead_time_days=10,
            unit_cost=Decimal("2.500000"),
            **product_common,
        ),
        SupplierProduct(
            id=ALTERNATIVE_A_PRODUCT_ID,
            supplier_id=ALTERNATIVE_A_SUPPLIER_ID,
            evidence_item_id=ALTERNATIVE_A_EVIDENCE_ID,
            product_code="AL-RECYCLED-A",
            name="Recycled aluminium billet A",
            description="Synthetic lower-carbon comparison product.",
            pcf_kgco2e_per_unit=Decimal("4.2"),
            circularity_score=Decimal(82),
            recycled_content_pct=Decimal(82),
            recyclable_pct=Decimal(99),
            evidence_quality_score=Decimal(88),
            lead_time_days=12,
            unit_cost=Decimal("2.550000"),
            **product_common,
        ),
        SupplierProduct(
            id=ALTERNATIVE_B_PRODUCT_ID,
            supplier_id=ALTERNATIVE_B_SUPPLIER_ID,
            evidence_item_id=ALTERNATIVE_B_EVIDENCE_ID,
            product_code="AL-RECYCLED-B",
            name="High-recycled aluminium billet B",
            description="Synthetic recommended lower-carbon comparison product.",
            pcf_kgco2e_per_unit=Decimal("2.4"),
            circularity_score=Decimal(94),
            recycled_content_pct=Decimal(95),
            recyclable_pct=Decimal(99),
            evidence_quality_score=Decimal(92),
            lead_time_days=14,
            unit_cost=Decimal("2.600000"),
            **product_common,
        ),
        SupplierProduct(
            id=ALTERNATIVE_C_PRODUCT_ID,
            supplier_id=ALTERNATIVE_C_SUPPLIER_ID,
            evidence_item_id=ALTERNATIVE_C_EVIDENCE_ID,
            product_code="AL-RECYCLED-C",
            name="Premium recycled aluminium billet C",
            description="Synthetic low-carbon product above the hard cost ceiling.",
            pcf_kgco2e_per_unit=Decimal("1.9"),
            circularity_score=Decimal(97),
            recycled_content_pct=Decimal(98),
            recyclable_pct=Decimal(100),
            evidence_quality_score=Decimal(95),
            lead_time_days=13,
            unit_cost=Decimal("2.725000"),
            **product_common,
        ),
    )

    raw_payload = {
        "row_key": "plant-b-recycled-aluminium-q3",
        "material_code": "RECYCLED-ALUMINIUM",
        "quantity": "10000",
        "unit": "kg",
        "activity_date": "2026-09-15",
        "supplier_code": "MAVERICK-CURRENT",
        "product_code": "AL-CURRENT",
        "unit_cost": "2.500000",
        "currency": "USD",
        "synthetic": "true",
    }
    raw_activity = RawActivityRecord(
        id=RAW_ACTIVITY_ID,
        company_id=DEMO_COMPANY_ID,
        data_source_id=ACTIVITY_SOURCE_ID,
        source_document_id=ACTIVITY_DOCUMENT_ID,
        row_key="plant-b-recycled-aluminium-q3",
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
        material_code="RECYCLED-ALUMINIUM",
        activity_date=date(2026, 9, 15),
        quantity=Decimal(10000),
        unit="kg",
        normalized_quantity=Decimal(10000),
        normalized_unit="kg",
        unit_cost=Decimal("2.500000"),
        currency="USD",
        status="valid",
    )
    factor = EmissionFactor(
        id=CURRENT_FACTOR_ID,
        company_id=DEMO_COMPANY_ID,
        metric_definition_id=EMISSIONS_METRIC_ID,
        evidence_item_id=FACTOR_EVIDENCE_ID,
        factor_code="AL-CURRENT-PCF",
        version="1.0.0",
        name="Synthetic current aluminium product carbon footprint",
        material_code="RECYCLED-ALUMINIUM",
        product_code="AL-CURRENT",
        geography="IN",
        factor_value=Decimal("8.6"),
        numerator_unit="kgCO2e",
        denominator_unit="kg",
        effective_from=date(2026, 1, 1),
        source_quality=Decimal("0.95"),
        factor_specificity=Decimal("1.00"),
        factor_recency=Decimal("1.00"),
        status="active",
    )
    assurance_standard = Standard(
        id=ASSURANCE_STANDARD_ID,
        company_id=DEMO_COMPANY_ID,
        source_document_id=ASSURANCE_STANDARD_DOCUMENT_ID,
        code="GHG-PROTOCOL-SCOPE-2-DEMO",
        version="2026-demo-v1",
        name="GHG Protocol Scope 2 summary with limited ESRS mapping (POC draft)",
        jurisdiction="GLOBAL",
        description=("Synthetic POC template only; not an assurance opinion or regulatory filing."),
        template={
            "fixture_version": "maverick-q3-2026-v1",
            "claim_order": ["S2-BOUNDARY", "S2-TOTAL", "ESRS-E1-TREND-LIMITED"],
            "synthetic": True,
        },
        effective_from=date(2026, 1, 1),
        is_active=True,
    )
    assurance_requirements = (
        DisclosureRequirement(
            id=ASSURANCE_BOUNDARY_REQUIREMENT_ID,
            company_id=DEMO_COMPANY_ID,
            standard_id=ASSURANCE_STANDARD_ID,
            requirement_code="S2-BOUNDARY",
            title="Organizational and site boundary",
            description="Identify the synthetic company, site, and reporting period.",
            sequence=1,
            claim_template="{company} reports location-based Scope 2 for {site}.",
            evidence_rules={
                "allowed_evidence_types": ["disclosure_support"],
                "required_context": ["company", "site", "reporting_period"],
            },
            minimum_confidence=Decimal("0.80000"),
            is_required=True,
            is_active=True,
        ),
        DisclosureRequirement(
            id=ASSURANCE_TOTAL_REQUIREMENT_ID,
            company_id=DEMO_COMPANY_ID,
            standard_id=ASSURANCE_STANDARD_ID,
            metric_definition_id=SCOPE2_METRIC_ID,
            requirement_code="S2-TOTAL",
            title="Location-based Scope 2 total",
            description="Report the verified location-based Scope 2 total.",
            sequence=2,
            claim_template="Location-based Scope 2 emissions were {scope2_total}.",
            evidence_rules={"fact_binding_required": True, "unit": "kgCO2e"},
            minimum_confidence=Decimal("0.80000"),
            is_required=True,
            is_active=True,
        ),
        DisclosureRequirement(
            id=ASSURANCE_REDUCTION_REQUIREMENT_ID,
            company_id=DEMO_COMPANY_ID,
            standard_id=ASSURANCE_STANDARD_ID,
            metric_definition_id=SCOPE2_METRIC_ID,
            requirement_code="ESRS-E1-TREND-LIMITED",
            title="Limited ESRS-style change claim",
            description="Requires a comparable verified prior-period total, which is absent.",
            sequence=3,
            claim_template="Scope 2 emissions fell {reduction_pct} year over year.",
            evidence_rules={
                "fact_binding_required": True,
                "comparable_prior_period_required": True,
                "missing_support_policy": "block",
            },
            minimum_confidence=Decimal("0.80000"),
            is_required=True,
            is_active=True,
        ),
    )
    flexible_load = FlexibleLoad(
        id=FLEXIBLE_LOAD_ID,
        company_id=DEMO_COMPANY_ID,
        site_id=DEMO_SITE_ID,
        code="BATCH-PROCESS-7",
        name="Batch Process 7",
        description="Synthetic flexible load; advisory scheduling only.",
        power_kw=Decimal(500),
        duration_minutes=120,
        energy_kwh=Decimal(1000),
        minimum_power_kw=Decimal(500),
        maximum_power_kw=Decimal(500),
        is_interruptible=False,
        load_metadata={
            "synthetic": True,
            "fixture_version": "maverick-q3-2026-v1",
            "actuation_authorized": False,
        },
        is_active=True,
    )
    dispatch_constraints = (
        OperatingConstraint(
            id=AVAILABILITY_CONSTRAINT_ID,
            company_id=DEMO_COMPANY_ID,
            flexible_load_id=FLEXIBLE_LOAD_ID,
            code="BATCH-7-AVAILABILITY",
            name="Batch Process 7 allowed interval",
            constraint_type="availability",
            is_hard=True,
            valid_from=datetime(2026, 10, 1, 8, tzinfo=UTC),
            valid_to=datetime(2026, 10, 1, 20, tzinfo=UTC),
            configuration={
                "earliest_start": "2026-10-01T08:00:00+00:00",
                "latest_finish": "2026-10-01T20:00:00+00:00",
            },
            is_active=True,
        ),
        OperatingConstraint(
            id=DEADLINE_CONSTRAINT_ID,
            company_id=DEMO_COMPANY_ID,
            flexible_load_id=FLEXIBLE_LOAD_ID,
            code="BATCH-7-MAX-DELAY",
            name="Batch Process 7 maximum delay",
            constraint_type="deadline",
            is_hard=True,
            valid_from=datetime(2026, 10, 1, 8, tzinfo=UTC),
            valid_to=datetime(2026, 10, 1, 20, tzinfo=UTC),
            configuration={
                "baseline_start": "2026-10-01T08:00:00+00:00",
                "maximum_delay_minutes": 240,
            },
            is_active=True,
        ),
        OperatingConstraint(
            id=BLACKOUT_CONSTRAINT_ID,
            company_id=DEMO_COMPANY_ID,
            flexible_load_id=FLEXIBLE_LOAD_ID,
            code="BATCH-7-BLACKOUT",
            name="Batch Process 7 maintenance blackout",
            constraint_type="blackout",
            is_hard=True,
            valid_from=datetime(2026, 10, 1, 10, tzinfo=UTC),
            valid_to=datetime(2026, 10, 1, 11, tzinfo=UTC),
            configuration={
                "start": "2026-10-01T10:00:00+00:00",
                "end": "2026-10-01T11:00:00+00:00",
            },
            is_active=True,
        ),
        OperatingConstraint(
            id=POWER_CONSTRAINT_ID,
            company_id=DEMO_COMPANY_ID,
            flexible_load_id=FLEXIBLE_LOAD_ID,
            code="BATCH-7-CAPACITY",
            name="Batch Process 7 available capacity",
            constraint_type="power",
            is_hard=True,
            valid_from=datetime(2026, 10, 1, 8, tzinfo=UTC),
            valid_to=datetime(2026, 10, 1, 20, tzinfo=UTC),
            configuration={
                "available_capacity_kw": "500",
                "start": "2026-10-01T08:00:00+00:00",
                "end": "2026-10-01T20:00:00+00:00",
            },
            is_active=True,
        ),
    )

    return (
        company,
        site,
        period,
        *actors,
        *semantic_entities,
        *metrics,
        *methods,
        *policies,
        *sources,
        *documents,
        *evidence,
        assurance_standard,
        *assurance_requirements,
        *suppliers,
        *products,
        raw_activity,
        activity,
        factor,
        flexible_load,
        *dispatch_constraints,
    )
