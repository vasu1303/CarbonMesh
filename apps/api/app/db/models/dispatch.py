from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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


class FlexibleLoad(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "flexible_loads"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_dispatch_loads_company_id_id"),
        UniqueConstraint("company_id", "site_id", "code", name="uq_dispatch_loads_site_code"),
        ForeignKeyConstraint(
            ["company_id", "site_id"],
            ["core.sites.company_id", "core.sites.id"],
            name="fk_dispatch_loads_company_site",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "semantic_entity_id"],
            ["semantic.semantic_entities.company_id", "semantic.semantic_entities.id"],
            name="fk_dispatch_loads_company_entity",
            ondelete="RESTRICT",
        ),
        CheckConstraint("power_kw > 0", name="power_positive"),
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
        CheckConstraint("energy_kwh > 0", name="energy_positive"),
        CheckConstraint(
            "minimum_power_kw IS NULL OR minimum_power_kw > 0",
            name="minimum_power_positive",
        ),
        CheckConstraint(
            "maximum_power_kw IS NULL OR maximum_power_kw >= power_kw",
            name="maximum_power_valid",
        ),
        Index("ix_dispatch_loads_site_active", "company_id", "site_id", "is_active"),
        {"schema": "dispatch"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    site_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    semantic_entity_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    power_kw: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    energy_kwh: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    minimum_power_kw: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    maximum_power_kw: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    is_interruptible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    load_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class OperatingConstraint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "operating_constraints"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_dispatch_constraints_company_id_id"),
        UniqueConstraint(
            "company_id", "flexible_load_id", "code", name="uq_dispatch_constraints_load_code"
        ),
        ForeignKeyConstraint(
            ["company_id", "flexible_load_id"],
            ["dispatch.flexible_loads.company_id", "dispatch.flexible_loads.id"],
            name="fk_dispatch_constraints_company_load",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "constraint_type IN ('availability', 'deadline', 'blackout', 'power', "
            "'cost', 'dependency')",
            name="constraint_type_allowed",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_dates_ordered",
        ),
        Index(
            "ix_dispatch_constraints_load_active",
            "company_id",
            "flexible_load_id",
            "is_active",
        ),
        {"schema": "dispatch"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    flexible_load_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    constraint_type: Mapped[str] = mapped_column(String(30), nullable=False)
    is_hard: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class GridForecast(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "grid_forecasts"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_dispatch_forecasts_company_id_id"),
        UniqueConstraint(
            "company_id",
            "site_id",
            "zone",
            "forecast_for",
            "issued_at",
            "temporal_granularity",
            name="uq_dispatch_forecasts_identity",
        ),
        UniqueConstraint(
            "company_id", "source_document_id", "point_hash", name="uq_dispatch_forecasts_hash"
        ),
        ForeignKeyConstraint(
            ["company_id", "site_id"],
            ["core.sites.company_id", "core.sites.id"],
            name="fk_dispatch_forecasts_company_site",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "source_document_id"],
            ["core.source_documents.company_id", "core.source_documents.id"],
            name="fk_dispatch_forecasts_company_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "evidence_item_id"],
            ["core.evidence_items.company_id", "core.evidence_items.id"],
            name="fk_dispatch_forecasts_company_evidence",
            ondelete="RESTRICT",
        ),
        CheckConstraint("intensity_gco2e_per_kwh >= 0", name="intensity_nonnegative"),
        CheckConstraint("point_hash ~ '^[0-9a-f]{64}$'", name="point_hash_sha256"),
        CheckConstraint(
            "temporal_granularity IN ('hourly', 'half_hourly', 'quarter_hourly')",
            name="granularity_allowed",
        ),
        Index(
            "ix_dispatch_forecasts_lookup",
            "company_id",
            "site_id",
            "zone",
            "forecast_for",
        ),
        {"schema": "dispatch"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    site_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_document_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_item_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    zone: Mapped[str] = mapped_column(String(100), nullable=False)
    forecast_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    temporal_granularity: Mapped[str] = mapped_column(String(30), nullable=False)
    emission_factor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    flow_traced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    intensity_gco2e_per_kwh: Mapped[Decimal] = mapped_column(Numeric(24, 9), nullable=False)
    point_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class DispatchScenario(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "dispatch_scenarios"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_dispatch_scenarios_company_id_id"),
        UniqueConstraint(
            "company_id", "analysis_signature", name="uq_dispatch_scenarios_signature"
        ),
        ForeignKeyConstraint(
            ["company_id", "site_id"],
            ["core.sites.company_id", "core.sites.id"],
            name="fk_dispatch_scenarios_company_site",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "flexible_load_id"],
            ["dispatch.flexible_loads.company_id", "dispatch.flexible_loads.id"],
            name="fk_dispatch_scenarios_company_load",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "agent_run_id"],
            ["ai.agent_runs.company_id", "ai.agent_runs.id"],
            name="fk_dispatch_scenarios_company_agent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "method_definition_id"],
            ["semantic.method_definitions.company_id", "semantic.method_definitions.id"],
            name="fk_dispatch_scenarios_company_method",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "policy_definition_id"],
            ["semantic.policy_definitions.company_id", "semantic.policy_definitions.id"],
            name="fk_dispatch_scenarios_company_policy",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "forecast_source_document_id"],
            ["core.source_documents.company_id", "core.source_documents.id"],
            name="fk_dispatch_scenarios_company_forecast_document",
            ondelete="RESTRICT",
        ),
        CheckConstraint("window_end > window_start", name="window_ordered"),
        CheckConstraint("analysis_signature ~ '^[0-9a-f]{64}$'", name="signature_sha256"),
        CheckConstraint("context_hash ~ '^[0-9a-f]{64}$'", name="context_hash_sha256"),
        CheckConstraint(
            "objective IN ('minimum_carbon', 'minimum_cost', 'carbon_then_cost')",
            name="objective_allowed",
        ),
        CheckConstraint(
            "status IN ('draft', 'optimized', 'recommended', 'no_feasible_window', "
            "'invalidated', 'closed')",
            name="status_allowed",
        ),
        Index("ix_dispatch_scenarios_context", "company_id", "site_id", "status"),
        {"schema": "dispatch"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    site_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    flexible_load_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    agent_run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    method_definition_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    policy_definition_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    forecast_source_document_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    objective: Mapped[str] = mapped_column(String(30), nullable=False)
    constraint_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    forecast_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    analysis_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="draft")


class DispatchRecommendation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "dispatch_recommendations"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_dispatch_recommendations_company_id_id"),
        UniqueConstraint(
            "company_id", "payload_hash", name="uq_dispatch_recommendations_payload_hash"
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "payload_hash",
            "analysis_signature",
            name="uq_dispatch_recommendations_approval_binding",
        ),
        ForeignKeyConstraint(
            ["company_id", "dispatch_scenario_id"],
            ["dispatch.dispatch_scenarios.company_id", "dispatch.dispatch_scenarios.id"],
            name="fk_dispatch_recommendations_company_scenario",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "ledger_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_dispatch_recommendations_company_ledger",
            ondelete="RESTRICT",
        ),
        CheckConstraint("recommended_end > recommended_start", name="recommended_window_ordered"),
        CheckConstraint("baseline_end > baseline_start", name="baseline_window_ordered"),
        CheckConstraint("expected_emissions_kgco2e >= 0", name="expected_emissions_nonnegative"),
        CheckConstraint("baseline_emissions_kgco2e >= 0", name="baseline_emissions_nonnegative"),
        CheckConstraint("avoided_kgco2e >= 0", name="avoided_nonnegative"),
        CheckConstraint("reduction_pct BETWEEN 0 AND 100", name="reduction_pct_range"),
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="payload_hash_sha256"),
        CheckConstraint("analysis_signature ~ '^[0-9a-f]{64}$'", name="signature_sha256"),
        CheckConstraint("actuation_authorized = false", name="advisory_only"),
        CheckConstraint(
            "status IN ('pending_approval', 'approved', 'rejected', 'invalidated')",
            name="status_allowed",
        ),
        Index(
            "uq_dispatch_recommendations_active_scenario",
            "company_id",
            "dispatch_scenario_id",
            unique=True,
            postgresql_where=text(
                "invalidated_at IS NULL AND status IN ('pending_approval', 'approved')"
            ),
        ),
        Index("ix_dispatch_recommendations_status", "company_id", "status"),
        {"schema": "dispatch"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    dispatch_scenario_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    ledger_event_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    recommended_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recommended_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_emissions_kgco2e: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    baseline_emissions_kgco2e: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    avoided_kgco2e: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    reduction_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    expected_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    currency: Mapped[str | None] = mapped_column(String(3))
    rationale_template: Mapped[str] = mapped_column(Text, nullable=False)
    impact_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    analysis_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="pending_approval")
    actuation_authorized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
