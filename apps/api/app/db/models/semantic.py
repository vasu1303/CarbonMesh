from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SemanticEntity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "semantic_entities"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_sem_entities_company_id_id"),
        UniqueConstraint("company_id", "entity_type", "key", name="uq_sem_entities_type_key"),
        CheckConstraint(
            "entity_type IN ('standard', 'material', 'unit', 'supplier', 'product', 'metric')",
            name="entity_type_allowed",
        ),
        Index("ix_sem_entities_company_type", "company_id", "entity_type", "is_active"),
        {"schema": "semantic"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    key: Mapped[str] = mapped_column(String(150), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    entity_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class SemanticAlias(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "semantic_aliases"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_sem_aliases_company_id_id"),
        UniqueConstraint("company_id", "alias", "locale", name="uq_sem_aliases_text_locale"),
        ForeignKeyConstraint(
            ["company_id", "semantic_entity_id"],
            ["semantic.semantic_entities.company_id", "semantic.semantic_entities.id"],
            name="fk_sem_aliases_company_entity",
            ondelete="RESTRICT",
        ),
        Index("ix_sem_aliases_company_entity", "company_id", "semantic_entity_id"),
        {"schema": "semantic"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    semantic_entity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    locale: Mapped[str] = mapped_column(String(20), nullable=False, server_default="en")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class MetricDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "metric_definitions"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_sem_metrics_company_id_id"),
        UniqueConstraint("company_id", "key", "version", name="uq_sem_metrics_key_version"),
        ForeignKeyConstraint(
            ["company_id", "semantic_entity_id"],
            ["semantic.semantic_entities.company_id", "semantic.semantic_entities.id"],
            name="fk_sem_metrics_company_entity",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version <> ''", name="version_nonempty"),
        Index("ix_sem_metrics_company_active", "company_id", "is_active"),
        {"schema": "semantic"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    semantic_entity_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    key: Mapped[str] = mapped_column(String(150), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_unit: Mapped[str] = mapped_column(String(50), nullable=False)
    dimensions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    handler: Mapped[str] = mapped_column(String(150), nullable=False)
    method_version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class MethodDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "method_definitions"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_sem_methods_company_id_id"),
        UniqueConstraint(
            "company_id", "method_type", "key", "version", name="uq_sem_methods_type_key_version"
        ),
        CheckConstraint(
            "method_type IN ('measurement', 'confidence', 'supplier_scoring', 'fact_binding')",
            name="method_type_allowed",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="effective_dates_valid"
        ),
        Index("ix_sem_methods_company_type", "company_id", "method_type", "is_active"),
        {"schema": "semantic"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    method_type: Mapped[str] = mapped_column(String(40), nullable=False)
    key: Mapped[str] = mapped_column(String(150), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
