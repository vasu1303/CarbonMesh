from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ai import AgentRun
from app.db.models.assurance import DisclosureRequirement, Standard
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
from app.db.models.dispatch import FlexibleLoad, OperatingConstraint
from app.db.models.ledger import LedgerEvent, LedgerEventEvidence, LineageEdge
from app.db.models.procurement import Supplier, SupplierProduct
from app.db.models.semantic import MethodDefinition, MetricDefinition, PolicyDefinition
from app.modules.sources.embedding import EMBEDDING_MODEL_ID, hash_embedding


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
    assurance_standard_id: UUID
    assurance_agent_run_id: UUID
    assurance_evidence_id: UUID
    scoring_method_id: UUID
    current_product_id: UUID
    alternative_product_id: UUID
    recommended_product_id: UUID
    expensive_product_id: UUID
    flexible_load_id: UUID
    dispatch_method_id: UUID
    dispatch_policy_id: UUID


SEED_IDS = ApiE2ESeedIds(
    company_id=_id(1),
    site_id=_id(2),
    reporting_period_id=_id(3),
    analyst_id=_id(4),
    procurement_manager_id=_id(5),
    approver_id=_id(6),
    measurement_id=_id(40),
    measurement_ledger_event_id=_id(54),
    assurance_standard_id=_id(90),
    assurance_agent_run_id=_id(94),
    assurance_evidence_id=_id(95),
    scoring_method_id=_id(22),
    current_product_id=_id(32),
    alternative_product_id=_id(33),
    recommended_product_id=_id(34),
    expensive_product_id=_id(36),
    flexible_load_id=_id(80),
    dispatch_method_id=_id(81),
    dispatch_policy_id=_id(82),
)


async def seed_api_e2e_data(session: AsyncSession) -> ApiE2ESeedIds:
    """Insert one complete, synthetic, lineage-backed CarbonMesh context.

    The caller owns the transaction. The E2E fixture recreates the eight schemas
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
    alternative_evidence_id = _id(15)
    carbon_metric_id = _id(20)
    grid_metric_id = _id(21)
    measurement_method_id = _id(23)
    current_supplier_id = _id(30)
    alternative_supplier_id = _id(31)
    expensive_supplier_id = _id(35)
    alternative_a_supplier_id = _id(37)
    raw_activity_id = _id(41)
    activity_id = _id(42)
    factor_id = _id(43)
    calculation_run_id = _id(44)
    emission_calculation_id = _id(45)
    source_event_id = _id(51)
    factor_event_id = _id(52)
    calculation_event_id = _id(53)
    assurance_support_text = (
        "Maverick Manufacturing (synthetic) reports verified emissions of "
        "86,000 kgCO2e for Plant B during Q3 2026."
    )

    objects = [
        Company(
            id=ids.company_id,
            code="MAVERICK-SYNTHETIC",
            name="Maverick Manufacturing (synthetic)",
            is_synthetic=True,
        ),
        Site(
            id=ids.site_id,
            company_id=ids.company_id,
            code="PLANT-B",
            name="Plant B",
            country_code="IN",
            timezone="Asia/Kolkata",
            electricity_maps_zone="IN",
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
        AgentRun(
            id=ids.assurance_agent_run_id,
            company_id=ids.company_id,
            actor_id=ids.analyst_id,
            trace_id="synthetic-assurance-e2e-run",
            workflow="assurance",
            stage="seeded_context",
            terminal_state="completed",
            context_envelope={
                "company_id": str(ids.company_id),
                "site_id": str(ids.site_id),
                "reporting_period_id": str(ids.reporting_period_id),
                "measurement_id": str(ids.measurement_id),
                "synthetic": True,
            },
            context_hash=_hash("synthetic-assurance-e2e-context"),
            plan={"modules": ["assurance"]},
            result={"seeded": True},
            telemetry={"synthetic": True},
            started_at=now,
            completed_at=now,
        ),
        DataSource(
            id=source_id,
            company_id=ids.company_id,
            site_id=ids.site_id,
            name="Maverick Q3 2026 synthetic evidence",
            source_type="synthetic",
            status="ready",
            external_reference="synthetic://maverick-q3-2026",
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
            filename="maverick-q3-2026-synthetic-evidence.json",
            content_type="application/json",
            checksum=_hash("maverick-q3-2026-synthetic-evidence"),
            size_bytes=1024,
            document_metadata={"synthetic": True},
            imported_at=now,
        ),
        EvidenceItem(
            id=current_evidence_id,
            company_id=ids.company_id,
            source_document_id=document_id,
            evidence_type="supplier_document",
            locator="synthetic:products/current-aluminium",
            content_text="Synthetic current aluminium PCF: 8.6 kgCO2e/kg.",
            checksum=_hash("synthetic-current-product-evidence"),
            evidence_metadata={"synthetic": True, "verified": True},
        ),
        EvidenceItem(
            id=alternative_evidence_id,
            company_id=ids.company_id,
            source_document_id=document_id,
            evidence_type="supplier_document",
            locator="synthetic:products/alternative-a",
            content_text="Synthetic recycled aluminium A PCF: 4.2 kgCO2e/kg.",
            checksum=_hash("synthetic-alternative-a-product-evidence"),
            evidence_metadata={"synthetic": True, "verified": True},
        ),
        EvidenceItem(
            id=recommended_evidence_id,
            company_id=ids.company_id,
            source_document_id=document_id,
            evidence_type="supplier_document",
            locator="synthetic:products/alternative-b",
            content_text="Synthetic recycled aluminium B PCF: 2.4 kgCO2e/kg.",
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
        EvidenceItem(
            id=ids.assurance_evidence_id,
            company_id=ids.company_id,
            source_document_id=document_id,
            evidence_type="disclosure_support",
            locator="synthetic:assurance/q3-2026-emissions-summary",
            content_text=assurance_support_text,
            checksum=_hash(assurance_support_text),
            evidence_metadata={
                "synthetic": True,
                "company_id": str(ids.company_id),
                "company": "Maverick Manufacturing (synthetic)",
                "site_id": str(ids.site_id),
                "site": "Plant B",
                "reporting_period_id": str(ids.reporting_period_id),
                "reporting_period": "Q3 2026",
                "requirement_codes": ["S2-BOUNDARY", "S2-TOTAL"],
            },
            embedding=hash_embedding(assurance_support_text),
            embedding_model=EMBEDDING_MODEL_ID,
            embedded_at=now,
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
            version="2.0.0",
            name="Electricity grid carbon intensity",
            canonical_unit="gCO2e/kWh",
            dimensions={"site": True, "provider_timestamp": True},
            handler="integrations.electricity_maps",
            method_version="electricity-maps-v4",
            description="Native Electricity Maps grid intensity in gCO2e/kWh.",
        ),
        Standard(
            id=ids.assurance_standard_id,
            company_id=ids.company_id,
            source_document_id=document_id,
            code="GHG-PROTOCOL-SCOPE-2-DEMO",
            version="2026-demo-v1",
            name="Synthetic GHG Protocol emissions summary",
            jurisdiction="GLOBAL",
            description="Synthetic POC disclosure template; not an assurance opinion.",
            template={"type": "scope_2_summary", "synthetic": True},
            effective_from=date(2026, 1, 1),
        ),
        DisclosureRequirement(
            id=_id(91),
            company_id=ids.company_id,
            standard_id=ids.assurance_standard_id,
            requirement_code="S2-BOUNDARY",
            title="Reporting boundary",
            description="Identify the company, site, and reporting period.",
            sequence=1,
            claim_template=("{company} reports emissions for {site} during {reporting_period}."),
            evidence_rules={"allowed_evidence_types": ["disclosure_support"]},
            minimum_confidence=Decimal("0.80000"),
        ),
        DisclosureRequirement(
            id=_id(92),
            company_id=ids.company_id,
            standard_id=ids.assurance_standard_id,
            metric_definition_id=carbon_metric_id,
            requirement_code="S2-TOTAL",
            title="Verified emissions total",
            description="Report the verified emissions total for the period.",
            sequence=2,
            claim_template="Verified emissions were {scope2_total}.",
            evidence_rules={
                "allowed_evidence_types": ["disclosure_support"],
                "fact_binding_required": True,
                "unit": "kgCO2e",
            },
            minimum_confidence=Decimal("0.90000"),
        ),
        DisclosureRequirement(
            id=_id(93),
            company_id=ids.company_id,
            standard_id=ids.assurance_standard_id,
            metric_definition_id=carbon_metric_id,
            requirement_code="S2-PRIOR-PERIOD",
            title="Prior-period comparison",
            description="Compare emissions with a verified prior period.",
            sequence=3,
            claim_template="Emissions decreased from the prior reporting period.",
            evidence_rules={
                "allowed_evidence_types": ["disclosure_support"],
                "comparable_prior_period_required": True,
            },
            minimum_confidence=Decimal("0.90000"),
        ),
        MethodDefinition(
            id=ids.scoring_method_id,
            company_id=ids.company_id,
            method_type="supplier_scoring",
            key="procurement.supplier_scoring",
            version="1.0.0",
            name="CarbonMesh deterministic supplier assessment",
            code_version="carbonmesh-api-0.1.0",
            configuration={
                "carbon": "0.40",
                "evidence": "0.25",
                "circularity": "0.20",
                "operational_fit": "0.15",
            },
            effective_from=date(2026, 1, 1),
        ),
        MethodDefinition(
            id=ids.dispatch_method_id,
            company_id=ids.company_id,
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
        ),
        PolicyDefinition(
            id=ids.dispatch_policy_id,
            company_id=ids.company_id,
            policy_type="dispatch",
            key="dispatch.advisory_only",
            version="1.0.0",
            name="Advisory-only dispatch policy",
            description="Synthetic policy that prohibits equipment actuation.",
            configuration={"actuation_authorized": False, "approval_required": True},
            effective_from=date(2026, 1, 1),
        ),
        MethodDefinition(
            id=measurement_method_id,
            company_id=ids.company_id,
            method_type="measurement",
            key="measurement.mass_times_factor",
            version="1.0.0",
            name="Mass multiplied by product emission factor",
            code_version="carbonmesh-api-0.1.0",
            configuration={"rounding": "ROUND_HALF_EVEN"},
            effective_from=date(2026, 1, 1),
        ),
        Supplier(
            id=current_supplier_id,
            company_id=ids.company_id,
            supplier_code="MAVERICK-CURRENT",
            name="Maverick Metals Baseline (Synthetic)",
            country_code="IN",
            supplier_metadata={"synthetic": True, "risk": "low", "risk_score": 18},
        ),
        Supplier(
            id=alternative_a_supplier_id,
            company_id=ids.company_id,
            supplier_code="CIRCULAR-AL-A",
            name="Circular Aluminium A (Synthetic)",
            country_code="IN",
            supplier_metadata={"synthetic": True, "risk": "low", "risk_score": 15},
        ),
        Supplier(
            id=alternative_supplier_id,
            company_id=ids.company_id,
            supplier_code="CIRCULAR-AL-B",
            name="Circular Aluminium B (Synthetic)",
            country_code="IN",
            supplier_metadata={"synthetic": True, "risk": "low", "risk_score": 12},
        ),
        Supplier(
            id=expensive_supplier_id,
            company_id=ids.company_id,
            supplier_code="CIRCULAR-AL-C",
            name="Premium Circular Aluminium C (Synthetic)",
            country_code="IN",
            supplier_metadata={"synthetic": True, "risk": "medium", "risk_score": 42},
        ),
        SupplierProduct(
            id=ids.current_product_id,
            company_id=ids.company_id,
            supplier_id=current_supplier_id,
            evidence_item_id=current_evidence_id,
            product_code="AL-CURRENT",
            name="Current recycled aluminium billet",
            material_code="RECYCLED-ALUMINIUM",
            category="metals",
            description="Synthetic current product.",
            pcf_kgco2e_per_unit=Decimal("8.6"),
            pcf_unit="kgCO2e/kg",
            circularity_score=Decimal(62),
            recycled_content_pct=Decimal(60),
            recyclable_pct=Decimal(98),
            evidence_quality_score=Decimal(94),
            lead_time_days=10,
            unit_cost=Decimal("2.50"),
            currency="USD",
            effective_from=date(2026, 1, 1),
        ),
        SupplierProduct(
            id=ids.alternative_product_id,
            company_id=ids.company_id,
            supplier_id=alternative_a_supplier_id,
            evidence_item_id=alternative_evidence_id,
            product_code="AL-RECYCLED-A",
            name="Recycled aluminium billet A",
            material_code="RECYCLED-ALUMINIUM",
            category="metals",
            description="Synthetic lower-carbon compatible product A.",
            pcf_kgco2e_per_unit=Decimal("4.2"),
            pcf_unit="kgCO2e/kg",
            circularity_score=Decimal(82),
            recycled_content_pct=Decimal(82),
            recyclable_pct=Decimal(99),
            evidence_quality_score=Decimal(88),
            lead_time_days=12,
            unit_cost=Decimal("2.55"),
            currency="USD",
            effective_from=date(2026, 1, 1),
        ),
        SupplierProduct(
            id=ids.recommended_product_id,
            company_id=ids.company_id,
            supplier_id=alternative_supplier_id,
            evidence_item_id=recommended_evidence_id,
            product_code="AL-RECYCLED-B",
            name="High-recycled aluminium billet B",
            material_code="RECYCLED-ALUMINIUM",
            category="metals",
            description="Synthetic lower-carbon compatible product.",
            pcf_kgco2e_per_unit=Decimal("2.4"),
            pcf_unit="kgCO2e/kg",
            circularity_score=Decimal(94),
            recycled_content_pct=Decimal(95),
            recyclable_pct=Decimal(99),
            evidence_quality_score=Decimal(92),
            lead_time_days=14,
            unit_cost=Decimal("2.60"),
            currency="USD",
            effective_from=date(2026, 1, 1),
        ),
        SupplierProduct(
            id=ids.expensive_product_id,
            company_id=ids.company_id,
            supplier_id=expensive_supplier_id,
            evidence_item_id=expensive_evidence_id,
            product_code="AL-RECYCLED-C",
            name="Premium recycled aluminium billet C",
            material_code="RECYCLED-ALUMINIUM",
            category="metals",
            description="Synthetic product deliberately above the hard cost ceiling.",
            pcf_kgco2e_per_unit=Decimal("1.9"),
            pcf_unit="kgCO2e/kg",
            circularity_score=Decimal(97),
            recycled_content_pct=Decimal(98),
            recyclable_pct=Decimal(100),
            evidence_quality_score=Decimal(95),
            lead_time_days=13,
            unit_cost=Decimal("2.725"),
            currency="USD",
            effective_from=date(2026, 1, 1),
        ),
        RawActivityRecord(
            id=raw_activity_id,
            company_id=ids.company_id,
            data_source_id=source_id,
            source_document_id=document_id,
            row_key="maverick-plant-b-q3-recycled-aluminium",
            row_number=1,
            raw_payload={
                "synthetic": True,
                "site": "Plant B",
                "material": "RECYCLED-ALUMINIUM",
                "quantity": "10000",
                "unit": "kg",
            },
            checksum=_hash("maverick-plant-b-q3-recycled-aluminium-row"),
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
            material_code="RECYCLED-ALUMINIUM",
            activity_date=date(2026, 8, 15),
            quantity=Decimal(10000),
            unit="kg",
            normalized_quantity=Decimal(10000),
            normalized_unit="kg",
            unit_cost=Decimal("2.50"),
            currency="USD",
            status="valid",
        ),
        EmissionFactor(
            id=factor_id,
            company_id=ids.company_id,
            metric_definition_id=carbon_metric_id,
            evidence_item_id=current_evidence_id,
            factor_code="AL-CURRENT-PCF",
            version="2026-Q3",
            name="Current recycled aluminium product carbon footprint",
            material_code="RECYCLED-ALUMINIUM",
            product_code="AL-CURRENT",
            geography="IN",
            factor_value=Decimal("8.6"),
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
            code_version="carbonmesh-api-0.1.0",
            rounding_policy="ROUND_HALF_EVEN:6",
            input_hash=_hash("maverick-measurement-input"),
            output_hash=_hash("maverick-measurement-output"),
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
            normalized_quantity=Decimal(10000),
            quantity_unit="kg",
            factor_value=Decimal("8.6"),
            factor_unit="kgCO2e/kg",
            emissions_kgco2e=Decimal(86000),
            formula="10000 kg * 8.6 kgCO2e/kg = 86000 kgCO2e",
            output_hash=_hash("maverick-emission-calculation-output"),
        ),
        LedgerEvent(
            id=source_event_id,
            company_id=ids.company_id,
            event_type="activity.normalized",
            entity_type="activity_record",
            entity_id=activity_id,
            payload={
                "label": "Normalized purchased material",
                "quantity": "10000",
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
                "factor": "8.6",
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
                "formula": "10000 * 8.6",
                "value": "86000",
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
                "value": "86000",
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
            value_kgco2e=Decimal(86000),
            unit="kgCO2e",
            confidence=Decimal("0.965"),
            status="verified",
            formula="10000 kg * 8.6 kgCO2e/kg = 86000 kgCO2e",
            output_hash=_hash("maverick-carbon-measurement"),
            verified_at=now,
        ),
        FlexibleLoad(
            id=ids.flexible_load_id,
            company_id=ids.company_id,
            site_id=ids.site_id,
            code="BATCH-PROCESS-7",
            name="Batch Process 7",
            description="Synthetic flexible load; advisory scheduling only.",
            power_kw=Decimal(500),
            duration_minutes=120,
            energy_kwh=Decimal(1000),
            minimum_power_kw=Decimal(500),
            maximum_power_kw=Decimal(500),
            is_interruptible=False,
            load_metadata={"synthetic": True, "actuation_authorized": False},
        ),
        OperatingConstraint(
            id=_id(83),
            company_id=ids.company_id,
            flexible_load_id=ids.flexible_load_id,
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
        ),
        OperatingConstraint(
            id=_id(84),
            company_id=ids.company_id,
            flexible_load_id=ids.flexible_load_id,
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
        ),
        OperatingConstraint(
            id=_id(85),
            company_id=ids.company_id,
            flexible_load_id=ids.flexible_load_id,
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
        ),
        OperatingConstraint(
            id=_id(86),
            company_id=ids.company_id,
            flexible_load_id=ids.flexible_load_id,
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
        (
            AgentRun,
            DataSource,
            MetricDefinition,
            MethodDefinition,
            PolicyDefinition,
            Supplier,
        ),
        (SourceDocument,),
        (EvidenceItem, Standard),
        (DisclosureRequirement,),
        (SupplierProduct,),
        (RawActivityRecord,),
        (ActivityRecord,),
        (EmissionFactor,),
        (CalculationRun,),
        (EmissionCalculation,),
        (LedgerEvent,),
        (CarbonMeasurement,),
        (FlexibleLoad,),
        (OperatingConstraint,),
        (LineageEdge, LedgerEventEvidence),
        (AuditLog,),
    )
    for model_types in insert_phases:
        session.add_all([item for item in objects if isinstance(item, model_types)])
        await session.flush()
    return ids
