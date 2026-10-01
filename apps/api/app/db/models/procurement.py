from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.core import Approval as _Approval
from app.db.models.ledger import FactBinding as _FactBinding

Approval = _Approval
FactBinding = _FactBinding


class Supplier(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_proc_suppliers_company_id_id"),
        UniqueConstraint("company_id", "supplier_code", name="uq_proc_suppliers_company_code"),
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_code_iso2"),
        CheckConstraint("status IN ('active', 'inactive')", name="status_allowed"),
        Index("ix_proc_suppliers_company_status", "company_id", "status"),
        {"schema": "procurement"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    supplier_code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    supplier_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class SupplierProduct(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_proc_products_company_id_id"),
        UniqueConstraint(
            "company_id", "supplier_id", "product_code", name="uq_proc_products_supplier_code"
        ),
        ForeignKeyConstraint(
            ["company_id", "supplier_id"],
            ["procurement.suppliers.company_id", "procurement.suppliers.id"],
            name="fk_proc_products_company_supplier",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "evidence_item_id"],
            ["core.evidence_items.company_id", "core.evidence_items.id"],
            name="fk_proc_products_company_evidence",
            ondelete="RESTRICT",
        ),
        CheckConstraint("pcf_kgco2e_per_unit >= 0", name="pcf_nonnegative"),
        CheckConstraint("circularity_score BETWEEN 0 AND 100", name="circularity_range"),
        CheckConstraint("recycled_content_pct BETWEEN 0 AND 100", name="recycled_pct_range"),
        CheckConstraint("recyclable_pct BETWEEN 0 AND 100", name="recyclable_pct_range"),
        CheckConstraint("evidence_quality_score BETWEEN 0 AND 100", name="evidence_score_range"),
        CheckConstraint("lead_time_days >= 0", name="lead_time_nonnegative"),
        CheckConstraint("unit_cost >= 0", name="cost_nonnegative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="effective_dates_valid"
        ),
        Index(
            "ix_proc_products_material",
            "company_id",
            "material_code",
            "category",
            "is_active",
        ),
        {"schema": "procurement"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    supplier_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_item_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    product_code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    material_code: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    pcf_kgco2e_per_unit: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    pcf_unit: Mapped[str] = mapped_column(String(100), nullable=False, server_default="kgCO2e/kg")
    circularity_score: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    recycled_content_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    recyclable_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    evidence_quality_score: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class ProcurementScenario(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "procurement_scenarios"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_proc_scenarios_company_id_id"),
        UniqueConstraint("company_id", "analysis_signature", name="uq_proc_scenarios_signature"),
        ForeignKeyConstraint(
            ["company_id", "site_id"],
            ["core.sites.company_id", "core.sites.id"],
            name="fk_proc_scenarios_company_site",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reporting_period_id"],
            ["core.reporting_periods.company_id", "core.reporting_periods.id"],
            name="fk_proc_scenarios_company_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "current_product_id"],
            ["procurement.supplier_products.company_id", "procurement.supplier_products.id"],
            name="fk_proc_scenarios_company_product",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "carbon_measurement_id"],
            ["carbon.carbon_measurements.company_id", "carbon.carbon_measurements.id"],
            name="fk_proc_scenarios_company_measurement",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "agent_run_id"],
            ["ai.agent_runs.company_id", "ai.agent_runs.id"],
            name="fk_proc_scenarios_company_agent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "method_definition_id"],
            ["semantic.method_definitions.company_id", "semantic.method_definitions.id"],
            name="fk_proc_scenarios_company_method",
            ondelete="RESTRICT",
        ),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("current_unit_cost > 0", name="current_cost_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        CheckConstraint(
            "max_cost_increase_pct BETWEEN 0 AND 100",
            name="max_cost_increase_range",
        ),
        CheckConstraint("max_lead_time_days >= 0", name="max_lead_time_nonnegative"),
        CheckConstraint(
            "minimum_circularity_score BETWEEN 0 AND 100", name="min_circularity_range"
        ),
        CheckConstraint(
            "carbon_weight >= 0 AND evidence_weight >= 0 AND circularity_weight >= 0 "
            "AND operational_fit_weight >= 0 AND "
            "carbon_weight + evidence_weight + circularity_weight + operational_fit_weight = 1",
            name="weights_total_one",
        ),
        CheckConstraint("analysis_signature ~ '^[0-9a-f]{64}$'", name="signature_sha256"),
        CheckConstraint(
            "status IN ('draft', 'assessed', 'recommended', 'closed')", name="status_allowed"
        ),
        Index(
            "ix_proc_scenarios_context", "company_id", "site_id", "reporting_period_id", "status"
        ),
        {"schema": "procurement"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    site_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    current_product_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    carbon_measurement_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    agent_run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    method_definition_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    quantity_unit: Mapped[str] = mapped_column(String(50), nullable=False)
    current_unit_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    max_cost_increase_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    max_lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_circularity_score: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    material_constraints: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    carbon_weight: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default="0.4000"
    )
    evidence_weight: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default="0.2500"
    )
    circularity_weight: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default="0.2000"
    )
    operational_fit_weight: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default="0.1500"
    )
    analysis_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")


class SupplierScore(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "supplier_scores"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_proc_scores_company_id_id"),
        UniqueConstraint(
            "company_id",
            "scenario_id",
            "supplier_product_id",
            "method_definition_id",
            name="uq_proc_scores_scenario_product_method",
        ),
        UniqueConstraint("company_id", "scenario_id", "rank", name="uq_proc_scores_scenario_rank"),
        ForeignKeyConstraint(
            ["company_id", "scenario_id"],
            ["procurement.procurement_scenarios.company_id", "procurement.procurement_scenarios.id"],
            name="fk_proc_scores_company_scenario",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "supplier_product_id"],
            ["procurement.supplier_products.company_id", "procurement.supplier_products.id"],
            name="fk_proc_scores_company_product",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "method_definition_id"],
            ["semantic.method_definitions.company_id", "semantic.method_definitions.id"],
            name="fk_proc_scores_company_method",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "agent_run_id"],
            ["ai.agent_runs.company_id", "ai.agent_runs.id"],
            name="fk_proc_scores_company_agent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "ledger_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_proc_scores_company_ledger",
            ondelete="RESTRICT",
        ),
        CheckConstraint("carbon_score BETWEEN 0 AND 100", name="carbon_score_range"),
        CheckConstraint("evidence_score BETWEEN 0 AND 100", name="evidence_score_range"),
        CheckConstraint("circularity_score BETWEEN 0 AND 100", name="circularity_score_range"),
        CheckConstraint("operational_fit_score BETWEEN 0 AND 100", name="fit_score_range"),
        CheckConstraint("total_score BETWEEN 0 AND 100", name="total_score_range"),
        CheckConstraint("rank IS NULL OR rank > 0", name="rank_positive"),
        Index("ix_proc_scores_scenario_feasible", "company_id", "scenario_id", "feasible"),
        {"schema": "procurement"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scenario_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    supplier_product_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    method_definition_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    agent_run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    ledger_event_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    carbon_score: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    evidence_score: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    circularity_score: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    operational_fit_score: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    total_score: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer)
    feasible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    infeasibility_reasons: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class Recommendation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "procurement_recommendations"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_proc_recommendations_company_id_id"),
        UniqueConstraint("company_id", "payload_hash", name="uq_proc_recommendations_payload_hash"),
        UniqueConstraint(
            "company_id",
            "id",
            "payload_hash",
            "analysis_signature",
            name="uq_proc_recommendations_approval_binding",
        ),
        ForeignKeyConstraint(
            ["company_id", "scenario_id"],
            ["procurement.procurement_scenarios.company_id", "procurement.procurement_scenarios.id"],
            name="fk_proc_recommendations_company_scenario",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "recommended_product_id"],
            ["procurement.supplier_products.company_id", "procurement.supplier_products.id"],
            name="fk_proc_recommendations_company_product",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "baseline_product_id"],
            ["procurement.supplier_products.company_id", "procurement.supplier_products.id"],
            name="fk_proc_recommendations_company_baseline",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "supplier_score_id"],
            ["procurement.supplier_scores.company_id", "procurement.supplier_scores.id"],
            name="fk_proc_recommendations_company_score",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "ledger_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_proc_recommendations_company_ledger",
            ondelete="RESTRICT",
        ),
        CheckConstraint("projected_footprint_kgco2e >= 0", name="footprint_nonnegative"),
        CheckConstraint("avoided_kgco2e >= 0", name="avoided_nonnegative"),
        CheckConstraint("reduction_pct BETWEEN 0 AND 100", name="reduction_pct_range"),
        CheckConstraint("analysis_signature ~ '^[0-9a-f]{64}$'", name="signature_sha256"),
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="payload_hash_sha256"),
        CheckConstraint(
            "status IN ('pending_approval', 'approved', 'rejected', 'invalidated')",
            name="status_allowed",
        ),
        Index(
            "uq_proc_recommendations_active_scenario",
            "company_id",
            "scenario_id",
            unique=True,
            postgresql_where=text(
                "invalidated_at IS NULL AND status IN ('pending_approval', 'approved')"
            ),
        ),
        Index("ix_proc_recommendations_status", "company_id", "status"),
        {"schema": "procurement"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scenario_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    recommended_product_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    baseline_product_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    supplier_score_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    ledger_event_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="pending_approval")
    projected_footprint_kgco2e: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    avoided_kgco2e: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    reduction_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    cost_delta_pct: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    lead_time_delta_days: Mapped[int] = mapped_column(Integer, nullable=False)
    rationale_template: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    impact_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# New code can use the explicit artifact name while existing services retain
# the established Recommendation class/import contract.
ProcurementRecommendation = Recommendation
