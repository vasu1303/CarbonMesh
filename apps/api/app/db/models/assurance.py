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


class Standard(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "standards"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_assurance_standards_company_id_id"),
        UniqueConstraint(
            "company_id", "code", "version", name="uq_assurance_standards_code_version"
        ),
        ForeignKeyConstraint(
            ["company_id", "source_document_id"],
            ["core.source_documents.company_id", "core.source_documents.id"],
            name="fk_assurance_standards_company_document",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_dates_valid",
        ),
        Index("ix_assurance_standards_active", "company_id", "is_active"),
        {"schema": "assurance"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_document_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    jurisdiction: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    template: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class DisclosureRequirement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "disclosure_requirements"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_assurance_requirements_company_id_id"),
        UniqueConstraint(
            "company_id",
            "standard_id",
            "requirement_code",
            name="uq_assurance_requirements_standard_code",
        ),
        ForeignKeyConstraint(
            ["company_id", "standard_id"],
            ["assurance.standards.company_id", "assurance.standards.id"],
            name="fk_assurance_requirements_company_standard",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "metric_definition_id"],
            ["semantic.metric_definitions.company_id", "semantic.metric_definitions.id"],
            name="fk_assurance_requirements_company_metric",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "minimum_confidence BETWEEN 0 AND 1", name="minimum_confidence_range"
        ),
        Index("ix_assurance_requirements_standard", "company_id", "standard_id", "sequence"),
        {"schema": "assurance"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    standard_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    metric_definition_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    requirement_code: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_template: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    minimum_confidence: Mapped[Decimal] = mapped_column(
        Numeric(6, 5), nullable=False, server_default="0"
    )
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class DisclosureDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "disclosure_drafts"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_assurance_drafts_company_id_id"),
        UniqueConstraint(
            "company_id", "standard_id", "reporting_period_id", "version", name="uq_assurance_drafts_version"
        ),
        ForeignKeyConstraint(
            ["company_id", "standard_id"],
            ["assurance.standards.company_id", "assurance.standards.id"],
            name="fk_assurance_drafts_company_standard",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "site_id"],
            ["core.sites.company_id", "core.sites.id"],
            name="fk_assurance_drafts_company_site",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reporting_period_id"],
            ["core.reporting_periods.company_id", "core.reporting_periods.id"],
            name="fk_assurance_drafts_company_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "agent_run_id"],
            ["ai.agent_runs.company_id", "ai.agent_runs.id"],
            name="fk_assurance_drafts_company_agent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "ledger_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_assurance_drafts_company_ledger",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("context_hash ~ '^[0-9a-f]{64}$'", name="context_hash_sha256"),
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="payload_hash_sha256"),
        CheckConstraint(
            "status IN ('draft', 'validating', 'blocked', 'pending_approval', "
            "'approved', 'rejected', 'invalidated')",
            name="status_allowed",
        ),
        Index("ix_assurance_drafts_context", "company_id", "site_id", "reporting_period_id"),
        Index("ix_assurance_drafts_status", "company_id", "status"),
        {"schema": "assurance"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    standard_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    site_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    agent_run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    ledger_event_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    narrative_template: Mapped[str] = mapped_column(Text, nullable=False)
    rendered_text: Mapped[str | None] = mapped_column(Text)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="draft")
    validation_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DisclosureClaim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "disclosure_claims"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_assurance_claims_company_id_id"),
        UniqueConstraint(
            "company_id", "disclosure_draft_id", "sequence", name="uq_assurance_claims_draft_sequence"
        ),
        ForeignKeyConstraint(
            ["company_id", "disclosure_draft_id"],
            ["assurance.disclosure_drafts.company_id", "assurance.disclosure_drafts.id"],
            name="fk_assurance_claims_company_draft",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "requirement_id"],
            [
                "assurance.disclosure_requirements.company_id",
                "assurance.disclosure_requirements.id",
            ],
            name="fk_assurance_claims_company_requirement",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "fact_binding_id"],
            ["ledger.fact_bindings.company_id", "ledger.fact_bindings.id"],
            name="fk_assurance_claims_company_binding",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "ledger_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_assurance_claims_company_ledger",
            ondelete="RESTRICT",
        ),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint(
            "claim_type IN ('numeric', 'qualitative', 'method', 'scope')",
            name="claim_type_allowed",
        ),
        CheckConstraint(
            "support_status IN ('supported', 'partially_supported', 'unsupported', "
            "'citation_invalid', 'context_mismatch', 'stale_fact', 'policy_blocked')",
            name="support_status_allowed",
        ),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint(
            "claim_type <> 'numeric' OR fact_binding_id IS NOT NULL OR "
            "support_status IN ('unsupported', 'policy_blocked')",
            name="numeric_claim_binding_required",
        ),
        Index("ix_assurance_claims_draft", "company_id", "disclosure_draft_id", "sequence"),
        Index("ix_assurance_claims_support", "company_id", "support_status"),
        {"schema": "assurance"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    disclosure_draft_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    requirement_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    fact_binding_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    ledger_event_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(30), nullable=False)
    claim_template: Mapped[str] = mapped_column(Text, nullable=False)
    rendered_text: Mapped[str | None] = mapped_column(Text)
    support_status: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    validation_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClaimCitation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "claim_citations"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_assurance_citations_company_id_id"),
        UniqueConstraint(
            "company_id",
            "disclosure_claim_id",
            "ledger_event_id",
            "evidence_item_id",
            name="uq_assurance_citations_claim_source",
        ),
        ForeignKeyConstraint(
            ["company_id", "disclosure_claim_id"],
            ["assurance.disclosure_claims.company_id", "assurance.disclosure_claims.id"],
            name="fk_assurance_citations_company_claim",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "ledger_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_assurance_citations_company_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "evidence_item_id"],
            ["core.evidence_items.company_id", "core.evidence_items.id"],
            name="fk_assurance_citations_company_evidence",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "ledger_event_id IS NOT NULL OR evidence_item_id IS NOT NULL",
            name="citation_source_present",
        ),
        CheckConstraint(
            "validation_status IN ('valid', 'invalid', 'stale', 'context_mismatch')",
            name="validation_status_allowed",
        ),
        Index("ix_assurance_citations_claim", "company_id", "disclosure_claim_id"),
        {"schema": "assurance"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    disclosure_claim_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    ledger_event_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    evidence_item_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    locator: Mapped[str | None] = mapped_column(String(500))
    validation_status: Mapped[str] = mapped_column(String(30), nullable=False)
    validation_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class EvidenceGap(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evidence_gaps"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_assurance_gaps_company_id_id"),
        ForeignKeyConstraint(
            ["company_id", "disclosure_draft_id"],
            ["assurance.disclosure_drafts.company_id", "assurance.disclosure_drafts.id"],
            name="fk_assurance_gaps_company_draft",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "disclosure_claim_id"],
            ["assurance.disclosure_claims.company_id", "assurance.disclosure_claims.id"],
            name="fk_assurance_gaps_company_claim",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "requirement_id"],
            [
                "assurance.disclosure_requirements.company_id",
                "assurance.disclosure_requirements.id",
            ],
            name="fk_assurance_gaps_company_requirement",
            ondelete="RESTRICT",
        ),
        CheckConstraint("severity IN ('info', 'warning', 'error')", name="severity_allowed"),
        CheckConstraint(
            "status IN ('open', 'resolved', 'waived')", name="status_allowed"
        ),
        Index("ix_assurance_gaps_status", "company_id", "status", "severity"),
        Index("ix_assurance_gaps_draft", "company_id", "disclosure_draft_id"),
        {"schema": "assurance"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    disclosure_draft_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    disclosure_claim_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    requirement_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
