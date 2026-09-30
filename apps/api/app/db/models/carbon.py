from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
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


class RawActivityRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "raw_activity_records"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_carbon_raw_company_id_id"),
        UniqueConstraint("company_id", "data_source_id", "row_key", name="uq_carbon_raw_source_row"),
        ForeignKeyConstraint(
            ["company_id", "data_source_id"],
            ["core.data_sources.company_id", "core.data_sources.id"],
            name="fk_carbon_raw_company_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "source_document_id"],
            ["core.source_documents.company_id", "core.source_documents.id"],
            name="fk_carbon_raw_company_document",
            ondelete="RESTRICT",
        ),
        CheckConstraint("row_number IS NULL OR row_number > 0", name="row_number_positive"),
        CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="checksum_sha256"),
        CheckConstraint(
            "import_status IN ('pending', 'accepted', 'rejected')", name="import_status_allowed"
        ),
        Index("ix_carbon_raw_document", "company_id", "source_document_id"),
        {"schema": "carbon"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    data_source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_document_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    row_key: Mapped[str] = mapped_column(String(255), nullable=False)
    row_number: Mapped[int | None] = mapped_column(Integer)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    import_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")


class ActivityRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "activity_records"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_carbon_activity_company_id_id"),
        UniqueConstraint("company_id", "raw_activity_record_id", name="uq_carbon_activity_raw"),
        ForeignKeyConstraint(
            ["company_id", "raw_activity_record_id"],
            ["carbon.raw_activity_records.company_id", "carbon.raw_activity_records.id"],
            name="fk_carbon_activity_company_raw",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "site_id"],
            ["core.sites.company_id", "core.sites.id"],
            name="fk_carbon_activity_company_site",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reporting_period_id"],
            ["core.reporting_periods.company_id", "core.reporting_periods.id"],
            name="fk_carbon_activity_company_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "metric_definition_id"],
            ["semantic.metric_definitions.company_id", "semantic.metric_definitions.id"],
            name="fk_carbon_activity_company_metric",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "supplier_product_id"],
            ["procurement.supplier_products.company_id", "procurement.supplier_products.id"],
            name="fk_carbon_activity_company_product",
            ondelete="RESTRICT",
        ),
        CheckConstraint("quantity >= 0", name="quantity_nonnegative"),
        CheckConstraint("normalized_quantity >= 0", name="normalized_quantity_nonnegative"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="unit_cost_nonnegative"),
        CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="currency_iso4217",
        ),
        CheckConstraint(
            "status IN ('valid', 'invalid', 'superseded')", name="status_allowed"
        ),
        Index(
            "ix_carbon_activity_context",
            "company_id",
            "site_id",
            "reporting_period_id",
            "material_code",
        ),
        {"schema": "carbon"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    raw_activity_record_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    site_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    metric_definition_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    supplier_product_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    material_code: Mapped[str] = mapped_column(String(100), nullable=False)
    activity_date: Mapped[date | None] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(50), nullable=False)
    normalized_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    normalized_unit: Mapped[str] = mapped_column(String(50), nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    currency: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="valid")


class EmissionFactor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "emission_factors"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_carbon_factors_company_id_id"),
        UniqueConstraint(
            "company_id", "factor_code", "version", "geography", name="uq_carbon_factor_version_geo"
        ),
        ForeignKeyConstraint(
            ["company_id", "metric_definition_id"],
            ["semantic.metric_definitions.company_id", "semantic.metric_definitions.id"],
            name="fk_carbon_factor_company_metric",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "evidence_item_id"],
            ["core.evidence_items.company_id", "core.evidence_items.id"],
            name="fk_carbon_factor_company_evidence",
            ondelete="RESTRICT",
        ),
        CheckConstraint("factor_value >= 0", name="value_nonnegative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="effective_dates_valid"
        ),
        CheckConstraint("source_quality BETWEEN 0 AND 1", name="source_quality_range"),
        CheckConstraint("factor_specificity BETWEEN 0 AND 1", name="specificity_range"),
        CheckConstraint("factor_recency BETWEEN 0 AND 1", name="recency_range"),
        CheckConstraint(
            "status IN ('active', 'inactive', 'superseded')", name="status_allowed"
        ),
        Index(
            "ix_carbon_factors_lookup",
            "company_id",
            "metric_definition_id",
            "material_code",
            "geography",
            "status",
        ),
        {"schema": "carbon"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    metric_definition_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_item_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    factor_code: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    material_code: Mapped[str | None] = mapped_column(String(100))
    product_code: Mapped[str | None] = mapped_column(String(100))
    geography: Mapped[str] = mapped_column(String(100), nullable=False, server_default="GLOBAL")
    factor_value: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    numerator_unit: Mapped[str] = mapped_column(String(50), nullable=False, server_default="kgCO2e")
    denominator_unit: Mapped[str] = mapped_column(String(50), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_quality: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    factor_specificity: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    factor_recency: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")


class CalculationRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "calculation_runs"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_carbon_calc_runs_company_id_id"),
        UniqueConstraint("company_id", "input_hash", name="uq_carbon_calc_runs_input_hash"),
        ForeignKeyConstraint(
            ["company_id", "agent_run_id"],
            ["carbon.agent_runs.company_id", "carbon.agent_runs.id"],
            name="fk_carbon_calc_runs_company_agent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reporting_period_id"],
            ["core.reporting_periods.company_id", "core.reporting_periods.id"],
            name="fk_carbon_calc_runs_company_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "method_definition_id"],
            ["semantic.method_definitions.company_id", "semantic.method_definitions.id"],
            name="fk_carbon_calc_runs_company_method",
            ondelete="RESTRICT",
        ),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash_sha256"),
        CheckConstraint(
            "output_hash IS NULL OR output_hash ~ '^[0-9a-f]{64}$'", name="output_hash_sha256"
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')", name="status_allowed"
        ),
        Index("ix_carbon_calc_runs_context", "company_id", "reporting_period_id", "status"),
        {"schema": "carbon"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    reporting_period_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    method_definition_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    method_version: Mapped[str] = mapped_column(String(50), nullable=False)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
    rounding_policy: Mapped[str] = mapped_column(String(100), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(100))


class EmissionCalculation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "emission_calculations"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_carbon_calcs_company_id_id"),
        UniqueConstraint(
            "company_id", "calculation_run_id", "activity_record_id", name="uq_carbon_calc_run_activity"
        ),
        ForeignKeyConstraint(
            ["company_id", "calculation_run_id"],
            ["carbon.calculation_runs.company_id", "carbon.calculation_runs.id"],
            name="fk_carbon_calc_company_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "activity_record_id"],
            ["carbon.activity_records.company_id", "carbon.activity_records.id"],
            name="fk_carbon_calc_company_activity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "emission_factor_id"],
            ["carbon.emission_factors.company_id", "carbon.emission_factors.id"],
            name="fk_carbon_calc_company_factor",
            ondelete="RESTRICT",
        ),
        CheckConstraint("normalized_quantity >= 0", name="quantity_nonnegative"),
        CheckConstraint("factor_value >= 0", name="factor_nonnegative"),
        CheckConstraint("emissions_kgco2e >= 0", name="emissions_nonnegative"),
        CheckConstraint("output_hash ~ '^[0-9a-f]{64}$'", name="output_hash_sha256"),
        Index("ix_carbon_calcs_run", "company_id", "calculation_run_id"),
        {"schema": "carbon"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    calculation_run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    activity_record_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    emission_factor_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    normalized_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    quantity_unit: Mapped[str] = mapped_column(String(50), nullable=False)
    factor_value: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    factor_unit: Mapped[str] = mapped_column(String(100), nullable=False)
    emissions_kgco2e: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class CarbonMeasurement(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "carbon_measurements"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_carbon_measurements_company_id_id"),
        UniqueConstraint("company_id", "output_hash", name="uq_carbon_measurements_output_hash"),
        ForeignKeyConstraint(
            ["company_id", "calculation_run_id"],
            ["carbon.calculation_runs.company_id", "carbon.calculation_runs.id"],
            name="fk_carbon_measurement_company_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "site_id"],
            ["core.sites.company_id", "core.sites.id"],
            name="fk_carbon_measurement_company_site",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reporting_period_id"],
            ["core.reporting_periods.company_id", "core.reporting_periods.id"],
            name="fk_carbon_measurement_company_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "metric_definition_id"],
            ["semantic.metric_definitions.company_id", "semantic.metric_definitions.id"],
            name="fk_carbon_measurement_company_metric",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "ledger_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_carbon_measurement_company_ledger",
            ondelete="RESTRICT",
        ),
        CheckConstraint("value_kgco2e >= 0", name="value_nonnegative"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint("output_hash ~ '^[0-9a-f]{64}$'", name="output_hash_sha256"),
        CheckConstraint(
            "status IN ('draft', 'verified', 'superseded', 'unsupported')", name="status_allowed"
        ),
        Index(
            "ix_carbon_measurements_context",
            "company_id",
            "site_id",
            "reporting_period_id",
            "status",
        ),
        {"schema": "carbon"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    calculation_run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    site_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    metric_definition_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    ledger_event_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    value_kgco2e: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(50), nullable=False, server_default="kgCO2e")
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataQualityIssue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "data_quality_issues"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_carbon_quality_company_id_id"),
        ForeignKeyConstraint(
            ["company_id", "raw_activity_record_id"],
            ["carbon.raw_activity_records.company_id", "carbon.raw_activity_records.id"],
            name="fk_carbon_quality_company_raw",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "activity_record_id"],
            ["carbon.activity_records.company_id", "carbon.activity_records.id"],
            name="fk_carbon_quality_company_activity",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'error')", name="severity_allowed"
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'waived')", name="status_allowed"
        ),
        Index("ix_carbon_quality_status", "company_id", "status", "severity"),
        {"schema": "carbon"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    raw_activity_record_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    activity_record_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    issue_type: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CarbonBaseline(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "carbon_baselines"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_carbon_baselines_company_id_id"),
        UniqueConstraint(
            "company_id",
            "site_id",
            "reporting_period_id",
            "metric_definition_id",
            name="uq_carbon_baseline_context",
        ),
        ForeignKeyConstraint(
            ["company_id", "site_id"],
            ["core.sites.company_id", "core.sites.id"],
            name="fk_carbon_baseline_company_site",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reporting_period_id"],
            ["core.reporting_periods.company_id", "core.reporting_periods.id"],
            name="fk_carbon_baseline_company_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "metric_definition_id"],
            ["semantic.metric_definitions.company_id", "semantic.metric_definitions.id"],
            name="fk_carbon_baseline_company_metric",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "source_measurement_id"],
            ["carbon.carbon_measurements.company_id", "carbon.carbon_measurements.id"],
            name="fk_carbon_baseline_company_measurement",
            ondelete="RESTRICT",
        ),
        CheckConstraint("value_kgco2e >= 0", name="value_nonnegative"),
        CheckConstraint("frozen_hash ~ '^[0-9a-f]{64}$'", name="frozen_hash_sha256"),
        {"schema": "carbon"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    site_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    metric_definition_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_measurement_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    value_kgco2e: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(50), nullable=False, server_default="kgCO2e")
    frozen_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class VarianceAlert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "variance_alerts"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_carbon_variances_company_id_id"),
        UniqueConstraint(
            "company_id", "carbon_measurement_id", "carbon_baseline_id", name="uq_carbon_variance_pair"
        ),
        ForeignKeyConstraint(
            ["company_id", "carbon_measurement_id"],
            ["carbon.carbon_measurements.company_id", "carbon.carbon_measurements.id"],
            name="fk_carbon_variance_company_measurement",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "carbon_baseline_id"],
            ["carbon.carbon_baselines.company_id", "carbon.carbon_baselines.id"],
            name="fk_carbon_variance_company_baseline",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'critical')", name="severity_allowed"
        ),
        CheckConstraint("status IN ('open', 'acknowledged', 'closed')", name="status_allowed"),
        Index("ix_carbon_variances_status", "company_id", "status", "severity"),
        {"schema": "carbon"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    carbon_measurement_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    carbon_baseline_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    variance_kgco2e: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")


class AgentRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_carbon_agent_runs_company_id_id"),
        UniqueConstraint("company_id", "trace_id", name="uq_carbon_agent_runs_trace"),
        ForeignKeyConstraint(
            ["company_id", "actor_id"],
            ["core.actors.company_id", "core.actors.id"],
            name="fk_carbon_agent_company_actor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "parent_run_id"],
            ["carbon.agent_runs.company_id", "carbon.agent_runs.id"],
            name="fk_carbon_agent_company_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "terminal_state IN ('needs_clarification', 'no_data', 'validation_error', "
            "'unsupported', 'no_feasible_option', 'failed_validation', 'budget_exhausted', "
            "'approval_invalidated', 'completed', 'running', 'failed')",
            name="terminal_state_allowed",
        ),
        CheckConstraint("model_calls >= 0 AND model_calls <= 3", name="model_calls_budget"),
        CheckConstraint("tool_calls >= 0 AND tool_calls <= 6", name="tool_calls_budget"),
        CheckConstraint("retry_count >= 0 AND retry_count <= 1", name="retry_budget"),
        Index("ix_carbon_agent_runs_trace", "trace_id"),
        Index("ix_carbon_agent_runs_state", "company_id", "terminal_state"),
        {"schema": "carbon"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    parent_run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    trace_id: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow: Mapped[str] = mapped_column(String(50), nullable=False)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    terminal_state: Mapped[str] = mapped_column(String(40), nullable=False, server_default="running")
    context_envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    telemetry: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
