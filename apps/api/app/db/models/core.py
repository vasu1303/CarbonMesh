from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
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


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("code", name="uq_core_companies_code"),
        {"schema": "core"},
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class Site(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_core_sites_company_id_id"),
        UniqueConstraint("company_id", "code", name="uq_core_sites_company_code"),
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_code_iso2"),
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name="latitude_range",
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="longitude_range",
        ),
        Index("ix_core_sites_company_active", "company_id", "is_active"),
        {"schema": "core"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    electricity_maps_zone: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class ReportingPeriod(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reporting_periods"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_core_periods_company_id_id"),
        UniqueConstraint(
            "company_id", "start_date", "end_date", name="uq_core_periods_company_dates"
        ),
        CheckConstraint("end_date >= start_date", name="valid_date_range"),
        CheckConstraint(
            "status IN ('open', 'closed', 'locked')", name="status_allowed"
        ),
        Index("ix_core_periods_company_status", "company_id", "status"),
        {"schema": "core"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")


class Actor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "actors"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_core_actors_company_id_id"),
        UniqueConstraint("company_id", "email", name="uq_core_actors_company_email"),
        CheckConstraint(
            "role IN ('sustainability_analyst', 'procurement_manager', 'approver', "
            "'auditor', 'system')",
            name="role_allowed",
        ),
        {"schema": "core"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class DataSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_core_sources_company_id_id"),
        UniqueConstraint("company_id", "name", name="uq_core_sources_company_name"),
        ForeignKeyConstraint(
            ["company_id", "site_id"],
            ["core.sites.company_id", "core.sites.id"],
            name="fk_core_sources_company_site",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_type IN ('csv', 'json', 'pdf', 'api', 'synthetic')",
            name="source_type_allowed",
        ),
        CheckConstraint(
            "status IN ('pending', 'ready', 'failed', 'archived')", name="status_allowed"
        ),
        Index("ix_core_sources_company_site", "company_id", "site_id"),
        {"schema": "core"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    site_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    external_reference: Mapped[str | None] = mapped_column(String(255))
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class SourceDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_core_documents_company_id_id"),
        UniqueConstraint("company_id", "checksum", name="uq_core_documents_company_checksum"),
        ForeignKeyConstraint(
            ["company_id", "data_source_id"],
            ["core.data_sources.company_id", "core.data_sources.id"],
            name="fk_core_documents_company_source",
            ondelete="RESTRICT",
        ),
        CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="checksum_sha256"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_core_documents_company_source", "company_id", "data_source_id"),
        {"schema": "core"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    data_source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    storage_uri: Mapped[str | None] = mapped_column(String(1000))
    document_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class EvidenceItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_core_evidence_company_id_id"),
        UniqueConstraint(
            "company_id", "source_document_id", "locator", name="uq_core_evidence_document_locator"
        ),
        ForeignKeyConstraint(
            ["company_id", "source_document_id"],
            ["core.source_documents.company_id", "core.source_documents.id"],
            name="fk_core_evidence_company_document",
            ondelete="RESTRICT",
        ),
        CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="checksum_sha256"),
        CheckConstraint(
            "((embedding IS NULL AND embedding_model IS NULL AND embedded_at IS NULL) OR "
            "(embedding IS NOT NULL AND embedding_model IS NOT NULL AND embedded_at IS NOT NULL))",
            name="embedding_metadata_complete",
        ),
        Index(
            "ix_core_evidence_company_document_type",
            "company_id",
            "source_document_id",
            "evidence_type",
        ),
        Index(
            "ix_core_evidence_items_embedding_cosine_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_where=text("embedding IS NOT NULL"),
        ),
        {"schema": "core"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_document_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(40), nullable=False)
    locator: Mapped[str] = mapped_column(String(500), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(768))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Approval(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Generic Preview-Approve-Commit record for every consequential module action.

    ``recommendation_id`` preserves the current procurement service contract while
    ``target_type``/``target_id`` provide the cross-module approval boundary.
    New assurance and dispatch services populate the generic target fields; legacy
    procurement writes remain valid until that service is migrated.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_core_approvals_company_id_id"),
        UniqueConstraint(
            "company_id", "idempotency_key", name="uq_core_approvals_idempotency"
        ),
        ForeignKeyConstraint(
            ["company_id", "recommendation_id", "preview_hash", "analysis_signature"],
            [
                "procurement.procurement_recommendations.company_id",
                "procurement.procurement_recommendations.id",
                "procurement.procurement_recommendations.payload_hash",
                "procurement.procurement_recommendations.analysis_signature",
            ],
            name="fk_core_approvals_recommendation_preview",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "requested_by"],
            ["core.actors.company_id", "core.actors.id"],
            name="fk_core_approvals_company_requester",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "decided_by"],
            ["core.actors.company_id", "core.actors.id"],
            name="fk_core_approvals_company_decider",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "ledger_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_core_approvals_company_ledger",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "policy_definition_id"],
            ["semantic.policy_definitions.company_id", "semantic.policy_definitions.id"],
            name="fk_core_approvals_company_policy",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "target_id IS NOT NULL OR recommendation_id IS NOT NULL",
            name="target_present",
        ),
        CheckConstraint("preview_hash ~ '^[0-9a-f]{64}$'", name="preview_hash_sha256"),
        CheckConstraint(
            "analysis_signature ~ '^[0-9a-f]{64}$'", name="signature_sha256"
        ),
        CheckConstraint(
            "context_hash IS NULL OR context_hash ~ '^[0-9a-f]{64}$'",
            name="context_hash_sha256",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'invalidated', 'expired')",
            name="status_allowed",
        ),
        CheckConstraint(
            "(status = 'pending' AND decided_by IS NULL AND decided_at IS NULL "
            "AND ledger_event_id IS NULL) OR "
            "(status IN ('approved', 'rejected') AND decided_by IS NOT NULL "
            "AND decided_at IS NOT NULL AND ledger_event_id IS NOT NULL) OR "
            "(status IN ('invalidated', 'expired') AND decided_by IS NULL "
            "AND decided_at IS NULL)",
            name="decision_fields_consistent",
        ),
        Index(
            "uq_core_approvals_pending_target",
            "company_id",
            "target_type",
            "target_id",
            unique=True,
            postgresql_where=text("status = 'pending' AND target_id IS NOT NULL"),
        ),
        Index(
            "uq_core_approvals_pending_recommendation",
            "company_id",
            "recommendation_id",
            unique=True,
            postgresql_where=text(
                "status = 'pending' AND recommendation_id IS NOT NULL"
            ),
        ),
        Index("ix_core_approvals_status", "company_id", "status"),
        {"schema": "core"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default="procurement_recommendation"
    )
    target_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    recommendation_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    requested_by: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    ledger_event_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    policy_definition_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    preview_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    preview_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    context_hash: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)


class AuditLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_core_audit_company_id_id"),
        ForeignKeyConstraint(
            ["company_id", "actor_id"],
            ["core.actors.company_id", "core.actors.id"],
            name="fk_core_audit_company_actor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "agent_run_id"],
            ["ai.agent_runs.company_id", "ai.agent_runs.id"],
            name="fk_core_audit_company_agent_run",
            ondelete="RESTRICT",
        ),
        Index("ix_core_audit_entity", "company_id", "entity_type", "entity_id"),
        Index("ix_core_audit_trace", "company_id", "trace_id"),
        {"schema": "core"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    agent_run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
