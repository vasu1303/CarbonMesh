from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
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

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AgentRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One bounded orchestration run across the CarbonMesh specialist graphs."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_ai_agent_runs_company_id_id"),
        UniqueConstraint("company_id", "trace_id", name="uq_ai_agent_runs_trace"),
        ForeignKeyConstraint(
            ["company_id", "actor_id"],
            ["core.actors.company_id", "core.actors.id"],
            name="fk_ai_agent_runs_company_actor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "parent_run_id"],
            ["ai.agent_runs.company_id", "ai.agent_runs.id"],
            name="fk_ai_agent_runs_company_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "terminal_state IN ('success', 'completed', 'running', 'failed', "
            "'needs_clarification', 'no_data', 'unsupported', 'policy_blocked', "
            "'provider_unavailable', 'budget_exhausted', 'validation_failed', "
            "'validation_error', 'failed_validation', 'approval_required', "
            "'approval_invalidated', 'no_feasible_option', "
            "'no_feasible_candidate', 'no_feasible_window', 'stale')",
            name="terminal_state_allowed",
        ),
        # The physical table accepts the largest approved cross-module budget.
        # Workflow-specific Pydantic/graph policies enforce the lower per-run limit.
        CheckConstraint("model_calls >= 0 AND model_calls <= 6", name="model_calls_budget"),
        CheckConstraint("tool_calls >= 0 AND tool_calls <= 20", name="tool_calls_budget"),
        CheckConstraint("retry_count >= 0 AND retry_count <= 1", name="retry_budget"),
        CheckConstraint("api_calls >= 0", name="api_calls_nonnegative"),
        CheckConstraint("input_tokens >= 0", name="input_tokens_nonnegative"),
        CheckConstraint("output_tokens >= 0", name="output_tokens_nonnegative"),
        CheckConstraint("latency_ms >= 0", name="latency_nonnegative"),
        CheckConstraint(
            "estimated_energy_wh IS NULL OR estimated_energy_wh >= 0",
            name="estimated_energy_nonnegative",
        ),
        CheckConstraint(
            "estimated_co2e_g IS NULL OR estimated_co2e_g >= 0",
            name="estimated_co2e_nonnegative",
        ),
        CheckConstraint(
            "context_hash IS NULL OR context_hash ~ '^[0-9a-f]{64}$'",
            name="context_hash_sha256",
        ),
        Index("ix_ai_agent_runs_trace", "trace_id"),
        Index("ix_ai_agent_runs_state", "company_id", "terminal_state"),
        {"schema": "ai"},
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
    context_hash: Mapped[str | None] = mapped_column(String(64))
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    telemetry: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    api_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    estimated_energy_wh: Mapped[Decimal | None] = mapped_column(Numeric(20, 9))
    estimated_co2e_g: Mapped[Decimal | None] = mapped_column(Numeric(20, 9))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))


class AgentRunStep(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Ordered node/tool telemetry used for replay and judge-facing run traces."""

    __tablename__ = "agent_run_steps"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_ai_agent_steps_company_id_id"),
        UniqueConstraint(
            "company_id", "agent_run_id", "sequence", name="uq_ai_agent_steps_run_sequence"
        ),
        ForeignKeyConstraint(
            ["company_id", "agent_run_id"],
            ["ai.agent_runs.company_id", "ai.agent_runs.id"],
            name="fk_ai_agent_steps_company_run",
            ondelete="RESTRICT",
        ),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint(
            "step_type IN ('context', 'planner', 'policy', 'graph', 'node', 'tool', "
            "'provider', 'retrieval', 'validation', 'interrupt', 'approval', "
            "'finalize')",
            name="step_type_allowed",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'blocked', 'skipped')",
            name="status_allowed",
        ),
        CheckConstraint("input_tokens >= 0", name="input_tokens_nonnegative"),
        CheckConstraint("output_tokens >= 0", name="output_tokens_nonnegative"),
        CheckConstraint("retry_count >= 0 AND retry_count <= 1", name="retry_budget"),
        CheckConstraint("latency_ms >= 0", name="latency_nonnegative"),
        Index("ix_ai_agent_steps_run", "company_id", "agent_run_id", "sequence"),
        Index("ix_ai_agent_steps_status", "company_id", "status"),
        {"schema": "ai"},
    )

    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("core.companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    agent_run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(30), nullable=False)
    graph_name: Mapped[str | None] = mapped_column(String(100))
    node_name: Mapped[str | None] = mapped_column(String(100))
    tool_name: Mapped[str | None] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    output_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
