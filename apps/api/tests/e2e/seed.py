from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import (
    ActivityRecord,
    CalculationRun,
    CarbonMeasurement,
    EmissionCalculation,
    EmissionFactor,
    RawActivityRecord,
)
from app.db.models.core import (
    Actor,
    AuditLog,
    Company,
    DataSource,
    EvidenceItem,
    ReportingPeriod,
    Site,
    SourceDocument,
)
from app.db.models.ledger import LedgerEvent, LedgerEventEvidence, LineageEdge
from app.db.models.procurement import Supplier, SupplierProduct
from app.db.models.semantic import MethodDefinition, MetricDefinition


def _id(suffix: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{suffix:012d}")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ApiE2ESeedIds:
    company_id: UUID
    site_id: UUID
    reporting_period_id: UUID
    analyst_id: UUID
    procurement_manager_id: UUID
    approver_id: UUID
    measurement_id: UUID
    measurement_ledger_event_id: UUID
    scoring_method_id: UUID
    current_product_id: UUID
    recommended_product_id: UUID
    expensive_product_id: UUID


SEED_IDS = ApiE2ESeedIds(
    company_id=_id(1),
    site_id=_id(2),
    reporting_period_id=_id(3),
    analyst_id=_id(4),
    procurement_manager_id=_id(5),
    approver_id=_id(6),
    measurement_id=_id(40),
    measurement_ledger_event_id=_id(54),
    scoring_method_id=_id(22),
    current_product_id=_id(32),
    recommended_product_id=_id(34),
    expensive_product_id=_id(36),
)


async def seed_api_e2e_data(session: AsyncSession) -> ApiE2ESeedIds:
    """Insert one complete, synthetic, lineage-backed CarbonMesh context.

    The caller owns the transaction. The E2E fixture recreates the five schemas
    for each test inside a temporary (or explicitly attested disposable)
    PostgreSQL database, so no row reaches the configured application database.
    """

    ids = SEED_IDS
    now = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)

    source_id = _id(10)
    document_id = _id(11)
    current_evidence_id = _id(12)
    recommended_evidence_id = _id(13)
    expensive_evidence_id = _id(14)
    carbon_metric_id = _id(20)
    grid_metric_id = _id(21)
    measurement_method_id = _id(23)
    current_supplier_id = _id(30)
    alternative_supplier_id = _id(31)
    expensive_supplier_id = _id(35)
    raw_activity_id = _id(41)
    activity_id = _id(42)
    factor_id = _id(43)
    calculation_run_id = _id(44)
    emission_calculation_id = _id(45)
    source_event_id = _id(51)
    factor_event_id = _id(52)
    calculation_event_id = _id(53)

    objects = [
        Company(
            id=ids.company_id,
            code="NOVA-SYNTHETIC",
            name="Nova Components Ltd",
            is_synthetic=True,
        ),
        Site(
            id=ids.site_id,
            company_id=ids.company_id,
            code="PLANT-B",
            name="Plant B",
            country_code="IN",
            timezone="Asia/Kolkata",
        ),
        ReportingPeriod(
            id=ids.reporting_period_id,
            company_id=ids.company_id,
            name="Q3 2026",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 9, 30),
            status="closed",
        ),
        Actor(
            id=ids.analyst_id,
            company_id=ids.company_id,
            email="analyst@synthetic.carbonmesh.invalid",
            display_name="Synthetic Sustainability Analyst",
            role="sustainability_analyst",
        ),
        Actor(
            id=ids.procurement_manager_id,
            company_id=ids.company_id,
            email="procurement@synthetic.carbonmesh.invalid",
            display_name="Synthetic Procurement Manager",
            role="procurement_manager",
        ),
        Actor(
            id=ids.approver_id,
            company_id=ids.company_id,
            email="approver@synthetic.carbonmesh.invalid",
            display_name="Synthetic Approver",
            role="approver",
        ),
        DataSource(
            id=source_id,
            company_id=ids.company_id,
            site_id=ids.site_id,
            name="Nova Q3 2026 synthetic evidence",
            source_type="synthetic",
            status="ready",
            external_reference="synthetic://nova-q3-2026",
            configuration={
                "synthetic": True,
                "electricity_maps_zone": "IN",
                "zone": "IN",
            },
            is_synthetic=True,
        ),
        SourceDocument(
            id=document_id,
            company_id=ids.company_id,
            data_source_id=source_id,
            filename="nova-q3-2026-synthetic-evidence.json",
            content_type="application/json",
            checksum=_hash("nova-q3-2026-synthetic-evidence"),
            size_bytes=1024,
            document_metadata={"synthetic": True},
            imported_at=now,
        ),
        EvidenceItem(
            id=current_evidence_id,
            company_id=ids.company_id,
            source_document_id=document_id,
            evidence_type="supplier_document",
            locator="synthetic:products/current-tray",
            content_text="Synthetic current packaging tray PCF: 2.8 kgCO2e/kg.",
            checksum=_hash("synthetic-current-product-evidence"),
            evidence_metadata={"synthetic": True, "verified": True},
        ),
        EvidenceItem(
            id=recommended_evidence_id,
            company_id=ids.company_id,
            source_document_id=document_id,
            evidence_type="supplier_document",
            locator="synthetic:products/alternative-b",
            content_text="Synthetic Alternative B PCF: 1.9 kgCO2e/kg.",
            checksum=_hash("synthetic-recommended-product-evidence"),
            evidence_metadata={"synthetic": True, "verified": True},
        ),
        EvidenceItem(
            id=expensive_evidence_id,
            company_id=ids.company_id,
            source_document_id=document_id,
            evidence_type="supplier_document",
            locator="synthetic:products/alternative-expensive",
            content_text="Synthetic low-carbon product outside the cost constraint.",
            checksum=_hash("synthetic-expensive-product-evidence"),
            evidence_metadata={"synthetic": True, "verified": True},
        ),
        MetricDefinition(
            id=carbon_metric_id,
            company_id=ids.company_id,
            key="emissions.scope3.category1",
            version="1.0.0",
            name="Purchased goods and services emissions",
            canonical_unit="kgCO2e",
            dimensions={"company": True, "site": True, "period": True},
            handler="measurement.calculate_scope3_category1",
            method_version="1.0.0",
            description="Synthetic POC metric.",
        ),
        MetricDefinition(
            id=grid_metric_id,
            company_id=ids.company_id,
            key="electricity.grid_carbon_intensity",
            version="1.0.0",
            name="Electricity grid carbon intensity",
            canonical_unit="kgCO2e/kWh",
            dimensions={"site": True, "provider_timestamp": True},
            handler="integrations.electricity_maps",
            method_version="electricity-maps-v4",
            description="Normalized from Electricity Maps gCO2eq/kWh.",
        ),
        MethodDefinition(
            id=ids.scoring_method_id,
            company_id=ids.company_id,
            method_type="supplier_scoring",
            key="procurement.supplier_assessment",
            version="1.0.0",
            name="CarbonMesh deterministic supplier assessment",
            code_version="poc-v1",
            configuration={
                "carbon": "0.40",
                "evidence": "0.25",
                "circularity": "0.20",
                "operational_fit": "0.15",
            },
            effective_from=date(2026, 1, 1),
        ),
        MethodDefinition(
            id=measurement_method_id,
            company_id=ids.company_id,
            method_type="measurement",
            key="measurement.mass_times_factor",
            version="1.0.0",
            name="Mass multiplied by product emission factor",
            code_version="poc-v1",
            configuration={"rounding": "ROUND_HALF_EVEN"},
            effective_from=date(2026, 1, 1),
        ),
        Supplier(
            id=current_supplier_id,
            company_id=ids.company_id,
            supplier_code="CURRENT-SUPPLIER",
            name="Current Packaging Co (Synthetic)",
            country_code="IN",
            supplier_metadata={"synthetic": True, "risk": "low", "risk_score": 18},
        ),
        Supplier(
            id=alternative_supplier_id,
            company_id=ids.company_id,
            supplier_code="ALTERNATIVE-B",
            name="Circular Trays B (Synthetic)",
            country_code="IN",
            supplier_metadata={"synthetic": True, "risk": "low", "risk_score": 12},
        ),
        Supplier(
            id=expensive_supplier_id,
            company_id=ids.company_id,
            supplier_code="ALTERNATIVE-C",
            name="Premium Circular Trays (Synthetic)",
            country_code="IN",
            supplier_metadata={"synthetic": True, "risk": "medium", "risk_score": 42},
        ),
        SupplierProduct(
            id=ids.current_product_id,
            company_id=ids.company_id,
            supplier_id=current_supplier_id,
            evidence_item_id=current_evidence_id,
            product_code="TRAY-CURRENT",
            name="Current packaging tray",
            material_code="PACKAGING-TRAY",
            category="packaging",
            description="Synthetic current product.",
            pcf_kgco2e_per_unit=Decimal("2.8"),
            pcf_unit="kgCO2e/kg",
            circularity_score=Decimal(55),
            recycled_content_pct=Decimal(30),
            recyclable_pct=Decimal(80),
            evidence_quality_score=Decimal(90),
            lead_time_days=12,
            unit_cost=Decimal("1.00"),
            currency="USD",
            effective_from=date(2026, 1, 1),
        ),
        SupplierProduct(
            id=ids.recommended_product_id,
            company_id=ids.company_id,
            supplier_id=alternative_supplier_id,
            evidence_item_id=recommended_evidence_id,
            product_code="TRAY-ALT-B",
            name="Alternative B packaging tray",
            material_code="PACKAGING-TRAY",
            category="packaging",
            description="Synthetic lower-carbon compatible product.",
            pcf_kgco2e_per_unit=Decimal("1.9"),
            pcf_unit="kgCO2e/kg",
            circularity_score=Decimal(88),
            recycled_content_pct=Decimal(75),
            recyclable_pct=Decimal(95),
            evidence_quality_score=Decimal(92),
            lead_time_days=14,
            unit_cost=Decimal("1.032"),
            currency="USD",
            effective_from=date(2026, 1, 1),
        ),
        SupplierProduct(
            id=ids.expensive_product_id,
            company_id=ids.company_id,
            supplier_id=expensive_supplier_id,
            evidence_item_id=expensive_evidence_id,
            product_code="TRAY-ALT-C",
            name="Alternative C premium tray",
            material_code="PACKAGING-TRAY",
            category="packaging",
            description="Synthetic product deliberately above the hard cost ceiling.",
            pcf_kgco2e_per_unit=Decimal("1.7"),
            pcf_unit="kgCO2e/kg",
            circularity_score=Decimal(93),
            recycled_content_pct=Decimal(85),
            recyclable_pct=Decimal(98),
            evidence_quality_score=Decimal(94),
            lead_time_days=13,
            unit_cost=Decimal("1.12"),
            currency="USD",
            effective_from=date(2026, 1, 1),
        ),
        RawActivityRecord(
            id=raw_activity_id,
            company_id=ids.company_id,
            data_source_id=source_id,
            source_document_id=document_id,
            row_key="nova-plant-b-q3-packaging-tray",
            row_number=1,
            raw_payload={
                "synthetic": True,
                "site": "Plant B",
                "material": "PACKAGING-TRAY",
                "quantity": "12000",
                "unit": "kg",
            },
            checksum=_hash("nova-plant-b-q3-packaging-tray-row"),
            import_status="accepted",
        ),
        ActivityRecord(
            id=activity_id,
            company_id=ids.company_id,
            raw_activity_record_id=raw_activity_id,
            site_id=ids.site_id,
            reporting_period_id=ids.reporting_period_id,
            metric_definition_id=carbon_metric_id,
            supplier_product_id=ids.current_product_id,
            material_code="PACKAGING-TRAY",
            activity_date=date(2026, 8, 15),
            quantity=Decimal(12000),
            unit="kg",
            normalized_quantity=Decimal(12000),
            normalized_unit="kg",
            unit_cost=Decimal("1.00"),
            currency="USD",
            status="valid",
        ),
        EmissionFactor(
            id=factor_id,
            company_id=ids.company_id,
            metric_definition_id=carbon_metric_id,
            evidence_item_id=current_evidence_id,
            factor_code="TRAY-CURRENT-PCF",
            version="2026-Q3",
            name="Current packaging tray product carbon footprint",
            material_code="PACKAGING-TRAY",
            product_code="TRAY-CURRENT",
            geography="IN",
            factor_value=Decimal("2.8"),
            numerator_unit="kgCO2e",
            denominator_unit="kg",
            effective_from=date(2026, 1, 1),
            source_quality=Decimal("0.95"),
            factor_specificity=Decimal("1.0"),
            factor_recency=Decimal("0.95"),
            status="active",
        ),
        CalculationRun(
            id=calculation_run_id,
            company_id=ids.company_id,
            reporting_period_id=ids.reporting_period_id,
            method_definition_id=measurement_method_id,
            method_version="1.0.0",
            code_version="poc-v1",
            rounding_policy="ROUND_HALF_EVEN:6",
            input_hash=_hash("nova-measurement-input"),
            output_hash=_hash("nova-measurement-output"),
            status="completed",
            started_at=now,
            completed_at=now,
            summary={"synthetic": True, "records": 1},
        ),
        EmissionCalculation(
            id=emission_calculation_id,
            company_id=ids.company_id,
            calculation_run_id=calculation_run_id,
            activity_record_id=activity_id,
            emission_factor_id=factor_id,
            normalized_quantity=Decimal(12000),
            quantity_unit="kg",
            factor_value=Decimal("2.8"),
            factor_unit="kgCO2e/kg",
            emissions_kgco2e=Decimal(33600),
            formula="12000 kg * 2.8 kgCO2e/kg = 33600 kgCO2e",
            output_hash=_hash("nova-emission-calculation-output"),
        ),
        LedgerEvent(
            id=source_event_id,
            company_id=ids.company_id,
            event_type="activity.normalized",
            entity_type="activity_record",
            entity_id=activity_id,
            payload={
                "label": "Normalized purchased material",
                "quantity": "12000",
                "unit": "kg",
                "source_row_id": str(raw_activity_id),
                "synthetic": True,
            },
            payload_hash=_hash("ledger-activity-normalized"),
            created_by=ids.analyst_id,
        ),
        LedgerEvent(
            id=factor_event_id,
            company_id=ids.company_id,
            event_type="factor.selected",
            entity_type="emission_factor",
            entity_id=factor_id,
            payload={
                "label": "Selected product-specific factor",
                "factor": "2.8",
                "unit": "kgCO2e/kg",
                "evidence_item_id": str(current_evidence_id),
            },
            payload_hash=_hash("ledger-factor-selected"),
            created_by=ids.analyst_id,
        ),
        LedgerEvent(
            id=calculation_event_id,
            company_id=ids.company_id,
            event_type="emissions.calculated",
            entity_type="emission_calculation",
            entity_id=emission_calculation_id,
            payload={
                "label": "Deterministic emissions calculation",
                "formula": "12000 * 2.8",
                "value": "33600",
                "unit": "kgCO2e",
            },
            payload_hash=_hash("ledger-emissions-calculated"),
            created_by=ids.analyst_id,
        ),
        LedgerEvent(
            id=ids.measurement_ledger_event_id,
            company_id=ids.company_id,
            event_type="measurement.verified",
            entity_type="carbon_measurement",
            entity_id=ids.measurement_id,
            payload={
                "label": "Verified Scope 3 Category 1 measurement",
                "value": "33600",
                "unit": "kgCO2e",
                "confidence": "0.965",
            },
            payload_hash=_hash("ledger-measurement-verified"),
            created_by=ids.analyst_id,
        ),
        CarbonMeasurement(
            id=ids.measurement_id,
            company_id=ids.company_id,
            calculation_run_id=calculation_run_id,
            site_id=ids.site_id,
            reporting_period_id=ids.reporting_period_id,
            metric_definition_id=carbon_metric_id,
            ledger_event_id=ids.measurement_ledger_event_id,
            value_kgco2e=Decimal(33600),
            unit="kgCO2e",
            confidence=Decimal("0.965"),
            status="verified",
            formula="12000 kg * 2.8 kgCO2e/kg = 33600 kgCO2e",
            output_hash=_hash("nova-carbon-measurement"),
            verified_at=now,
        ),
        LineageEdge(
            id=_id(61),
            company_id=ids.company_id,
            parent_event_id=source_event_id,
            child_event_id=calculation_event_id,
            relationship_type="activity_input",
            edge_metadata={"synthetic": True},
        ),
        LineageEdge(
            id=_id(62),
            company_id=ids.company_id,
            parent_event_id=factor_event_id,
            child_event_id=calculation_event_id,
            relationship_type="factor_input",
            edge_metadata={"synthetic": True},
        ),
        LineageEdge(
            id=_id(63),
            company_id=ids.company_id,
            parent_event_id=calculation_event_id,
            child_event_id=ids.measurement_ledger_event_id,
            relationship_type="produces",
            edge_metadata={"synthetic": True},
        ),
        LedgerEventEvidence(
            company_id=ids.company_id,
            ledger_event_id=factor_event_id,
            evidence_item_id=current_evidence_id,
            relevance="Product-specific PCF evidence",
        ),
        AuditLog(
            id=_id(70),
            company_id=ids.company_id,
            actor_id=ids.analyst_id,
            action="measurement.verified",
            entity_type="carbon_measurement",
            entity_id=ids.measurement_id,
            trace_id="synthetic-measurement-trace",
            details={
                "synthetic": True,
                "ledger_event_id": str(ids.measurement_ledger_event_id),
            },
        ),
    ]
    insert_phases = (
        (Company,),
        (Site, ReportingPeriod, Actor),
        (DataSource, MetricDefinition, MethodDefinition, Supplier),
        (SourceDocument,),
        (EvidenceItem,),
        (SupplierProduct,),
        (RawActivityRecord,),
        (ActivityRecord,),
        (EmissionFactor,),
        (CalculationRun,),
        (EmissionCalculation,),
        (LedgerEvent,),
        (CarbonMeasurement,),
        (LineageEdge, LedgerEventEvidence),
        (AuditLog,),
    )
    for model_types in insert_phases:
        session.add_all([item for item in objects if isinstance(item, model_types)])
        await session.flush()
    return ids
