from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from app.db.models.carbon import CarbonMeasurement
from app.db.models.core import EvidenceItem
from app.db.models.procurement import (
    ProcurementScenario,
    Supplier,
    SupplierProduct,
    SupplierScore,
)
from app.db.models.semantic import MethodDefinition
from app.modules.procurement.repository import ProductRecord
from app.modules.procurement.review import build_review_source_state, review_source_hash


def uid(suffix: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{suffix:012d}")


def product_record(*, suffix: int, pcf: str) -> ProductRecord:
    company_id = uid(1)
    supplier = Supplier(
        id=uid(suffix),
        company_id=company_id,
        supplier_code=f"SUPPLIER-{suffix}",
        name=f"Supplier {suffix}",
        country_code="IN",
        status="active",
        supplier_metadata={"risk": "low"},
    )
    evidence = EvidenceItem(
        id=uid(suffix + 1),
        company_id=company_id,
        source_document_id=uid(90),
        evidence_type="supplier_document",
        locator=f"synthetic:{suffix}",
        content_text=f"Evidence {suffix}",
        checksum=f"{suffix:064x}",
        evidence_metadata={"synthetic": True},
    )
    product = SupplierProduct(
        id=uid(suffix + 2),
        company_id=company_id,
        supplier_id=supplier.id,
        evidence_item_id=evidence.id,
        product_code=f"PRODUCT-{suffix}",
        name=f"Product {suffix}",
        material_code="PACKAGING-TRAY",
        category="packaging",
        pcf_kgco2e_per_unit=Decimal(pcf),
        pcf_unit="kgCO2e/kg",
        circularity_score=Decimal(80),
        recycled_content_pct=Decimal(70),
        recyclable_pct=Decimal(90),
        evidence_quality_score=Decimal(90),
        lead_time_days=12,
        unit_cost=Decimal("1.00"),
        currency="USD",
        effective_from=date(2026, 1, 1),
        is_active=True,
    )
    return ProductRecord(product=product, supplier=supplier, evidence=evidence)


def test_review_source_fingerprint_detects_product_evidence_and_method_changes() -> None:
    baseline = product_record(suffix=10, pcf="2.8")
    selected = product_record(suffix=20, pcf="1.9")
    unselected = product_record(suffix=40, pcf="2.1")
    scenario = ProcurementScenario(
        id=uid(30),
        company_id=uid(1),
        site_id=uid(31),
        reporting_period_id=uid(32),
        current_product_id=baseline.product.id,
        carbon_measurement_id=uid(33),
        method_definition_id=uid(34),
        quantity=Decimal(12000),
        quantity_unit="kg",
        current_unit_cost=Decimal("1.00"),
        currency="USD",
        max_cost_increase_pct=Decimal(5),
        max_lead_time_days=20,
        minimum_circularity_score=Decimal(50),
        material_constraints={"allowed_material_codes": [], "excluded_risk_levels": []},
        carbon_weight=Decimal("0.4"),
        evidence_weight=Decimal("0.25"),
        circularity_weight=Decimal("0.2"),
        operational_fit_weight=Decimal("0.15"),
        analysis_signature="a" * 64,
        frozen_context={},
        status="recommended",
    )
    method = MethodDefinition(
        id=uid(34),
        company_id=uid(1),
        method_type="supplier_scoring",
        key="procurement.supplier_assessment",
        version="1.0.0",
        name="Supplier assessment",
        code_version="carbonmesh-api-0.1.0",
        configuration={"carbon": "0.40"},
        effective_from=date(2026, 1, 1),
        is_active=True,
    )
    score = SupplierScore(
        id=uid(35),
        company_id=uid(1),
        scenario_id=scenario.id,
        supplier_product_id=selected.product.id,
        method_definition_id=method.id,
        carbon_score=Decimal("32.1429"),
        evidence_score=Decimal(90),
        circularity_score=Decimal(80),
        operational_fit_score=Decimal(40),
        total_score=Decimal("57.3572"),
        rank=1,
        feasible=True,
        infeasibility_reasons=[],
    )
    unselected_score = SupplierScore(
        id=uid(45),
        company_id=uid(1),
        scenario_id=scenario.id,
        supplier_product_id=unselected.product.id,
        method_definition_id=method.id,
        carbon_score=Decimal("25.0000"),
        evidence_score=Decimal(90),
        circularity_score=Decimal(80),
        operational_fit_score=Decimal(40),
        total_score=Decimal("54.5000"),
        rank=2,
        feasible=True,
        infeasibility_reasons=[],
    )
    measurement = CarbonMeasurement(
        id=scenario.carbon_measurement_id,
        company_id=uid(1),
        calculation_run_id=uid(36),
        site_id=scenario.site_id,
        reporting_period_id=scenario.reporting_period_id,
        metric_definition_id=uid(37),
        ledger_event_id=uid(38),
        value_kgco2e=Decimal("33600.000000"),
        unit="kgCO2e",
        confidence=Decimal("0.90000"),
        status="verified",
        formula="12000 kg * 2.8 kgCO2e/kg",
        output_hash="b" * 64,
        verified_at=None,
    )
    candidate_assessments = [(score, selected), (unselected_score, unselected)]
    eligible_candidates = [selected, unselected]

    def fingerprint() -> str:
        return review_source_hash(
            build_review_source_state(
                scenario=scenario,
                baseline=baseline,
                selected=selected,
                score=score,
                method=method,
                measurement=measurement,
                candidate_assessments=candidate_assessments,
                eligible_candidates=eligible_candidates,
            )
        )

    original = fingerprint()
    selected.product.pcf_kgco2e_per_unit = Decimal("1.8")
    assert fingerprint() != original

    selected.product.pcf_kgco2e_per_unit = Decimal("1.9")
    assert selected.evidence is not None
    selected.evidence.content_text = "Revised evidence"
    assert fingerprint() != original

    selected.evidence.content_text = "Evidence 20"
    unselected.product.pcf_kgco2e_per_unit = Decimal("2.0")
    assert fingerprint() != original

    unselected.product.pcf_kgco2e_per_unit = Decimal("2.1")
    measurement.output_hash = "c" * 64
    assert fingerprint() != original

    measurement.output_hash = "b" * 64
    method.configuration = {"carbon": "0.35"}
    assert fingerprint() != original

    method.configuration = {"carbon": "0.40"}
    eligible_candidates.append(product_record(suffix=50, pcf="1.7"))
    assert fingerprint() != original
