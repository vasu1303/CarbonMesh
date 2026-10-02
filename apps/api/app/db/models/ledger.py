from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class LedgerEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "ledger_events"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_ledger_events_company_id_id"),
        ForeignKeyConstraint(
            ["company_id", "created_by"],
            ["core.actors.company_id", "core.actors.id"],
            name="fk_ledger_events_company_actor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "agent_run_id"],
            ["ai.agent_runs.company_id", "ai.agent_runs.id"],
            name="fk_ledger_events_company_agent_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "supersedes_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_ledger_events_company_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "supersedes_event_id IS NULL OR supersedes_event_id <> id", name="not_self_superseding"
        ),
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="payload_hash_sha256"),
        CheckConstraint(
            "analysis_signature IS NULL OR analysis_signature ~ '^[0-9a-f]{64}$'",
            name="analysis_signature_sha256",
        ),
        Index("ix_ledger_events_entity", "company_id", "entity_type", "entity_id"),
        Index("ix_ledger_events_type_created", "company_id", "event_type", "created_at"),
        {"schema": "ledger"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_signature: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    agent_run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    supersedes_event_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))


class LineageEdge(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "lineage_edges"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_ledger_edges_company_id_id"),
        UniqueConstraint(
            "company_id",
            "parent_event_id",
            "child_event_id",
            "relationship_type",
            name="uq_ledger_edges_relationship",
        ),
        ForeignKeyConstraint(
            ["company_id", "parent_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_ledger_edges_company_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "child_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_ledger_edges_company_child",
            ondelete="RESTRICT",
        ),
        CheckConstraint("parent_event_id <> child_event_id", name="not_self_referencing"),
        Index("ix_ledger_edges_parent", "company_id", "parent_event_id"),
        Index("ix_ledger_edges_child", "company_id", "child_event_id"),
        {"schema": "ledger"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_event_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    child_event_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(100), nullable=False)
    edge_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class LedgerEventEvidence(CreatedAtMixin, Base):
    __tablename__ = "ledger_event_evidence"
    __table_args__ = (
        PrimaryKeyConstraint(
            "ledger_event_id", "evidence_item_id", name="pk_ledger_event_evidence"
        ),
        ForeignKeyConstraint(
            ["company_id", "ledger_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_ledger_event_evidence_company_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "evidence_item_id"],
            ["core.evidence_items.company_id", "core.evidence_items.id"],
            name="fk_ledger_event_evidence_company_item",
            ondelete="RESTRICT",
        ),
        Index("ix_ledger_event_evidence_company", "company_id", "evidence_item_id"),
        {"schema": "ledger"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ledger_event_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_item_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    relevance: Mapped[str | None] = mapped_column(String(255))


class FactBinding(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Resolve a generated placeholder to an immutable verified ledger fact."""

    __tablename__ = "fact_bindings"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_ledger_bindings_company_id_id"),
        UniqueConstraint(
            "company_id",
            "recommendation_id",
            "placeholder",
            name="uq_ledger_bindings_recommendation_placeholder",
        ),
        UniqueConstraint(
            "company_id",
            "artifact_type",
            "artifact_id",
            "placeholder",
            name="uq_ledger_bindings_artifact_placeholder",
        ),
        ForeignKeyConstraint(
            ["company_id", "recommendation_id"],
            [
                "procurement.procurement_recommendations.company_id",
                "procurement.procurement_recommendations.id",
            ],
            name="fk_ledger_bindings_company_recommendation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "agent_run_id"],
            ["ai.agent_runs.company_id", "ai.agent_runs.id"],
            name="fk_ledger_bindings_company_agent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "ledger_event_id"],
            ["ledger.ledger_events.company_id", "ledger.ledger_events.id"],
            name="fk_ledger_bindings_company_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "evidence_item_id"],
            ["core.evidence_items.company_id", "core.evidence_items.id"],
            name="fk_ledger_bindings_company_evidence",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "artifact_id IS NOT NULL OR recommendation_id IS NOT NULL",
            name="artifact_present",
        ),
        CheckConstraint("placeholder ~ '^fact_[a-z0-9_]+$'", name="placeholder_format"),
        CheckConstraint(
            "binding_hash IS NULL OR binding_hash ~ '^[0-9a-f]{64}$'",
            name="binding_hash_sha256",
        ),
        CheckConstraint(
            "context_hash IS NULL OR context_hash ~ '^[0-9a-f]{64}$'",
            name="context_hash_sha256",
        ),
        Index("ix_ledger_bindings_agent", "company_id", "agent_run_id"),
        Index("ix_ledger_bindings_artifact", "company_id", "artifact_type", "artifact_id"),
        {"schema": "ledger"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_type: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default="procurement_recommendation"
    )
    artifact_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    recommendation_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    agent_run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    ledger_event_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    evidence_item_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    placeholder: Mapped[str] = mapped_column(String(150), nullable=False)
    value_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    display_value: Mapped[str] = mapped_column(String(255), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50))
    context_hash: Mapped[str | None] = mapped_column(String(64))
    binding_hash: Mapped[str | None] = mapped_column(String(64))
