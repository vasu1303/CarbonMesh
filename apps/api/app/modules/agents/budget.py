"""Runtime-enforced call and latency budgets for bounded graph execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from app.modules.agents.graph_contracts import (
    BudgetLimits,
    BudgetProfileName,
    budget_limits_for,
)


class RuntimeBudgetExhausted(RuntimeError):
    """Raised before an operation that would exceed a frozen run budget."""

    def __init__(self, dimension: str) -> None:
        super().__init__(f"{dimension} budget exhausted")
        self.dimension = dimension


@dataclass(slots=True)
class RuntimeBudgetCounter:
    limits: BudgetLimits
    model_calls: int = 0
    tool_calls: int = 0
    repairs: int = 0
    # Resolve the clock when each budget is created so deterministic tests can
    # replace it and recovery logic never shares an import-time clock binding.
    started_clock: float = field(default_factory=lambda: monotonic())
    deadline_clock: float | None = None

    def constrain_latency(
        self,
        *,
        cumulative_elapsed_ms: int,
        segment_limit_ms: int | None = None,
    ) -> None:
        """Restore a persisted run deadline and optionally cap this segment.

        Human wait time between approval segments is deliberately excluded, but
        active execution time is cumulative across every durable continuation.
        """

        if cumulative_elapsed_ms < 0:
            raise ValueError("cumulative elapsed time cannot be negative")
        if segment_limit_ms is not None and segment_limit_ms < 0:
            raise ValueError("segment latency limit cannot be negative")
        now = monotonic()
        self.started_clock = now - (cumulative_elapsed_ms / 1000)
        self.deadline_clock = (
            now + (segment_limit_ms / 1000)
            if segment_limit_ms is not None
            else None
        )

    def check_deadline(self) -> None:
        if self.remaining_seconds <= 0:
            raise RuntimeBudgetExhausted("latency")

    def consume_model(self) -> None:
        self.check_deadline()
        if self.model_calls >= self.limits.max_model_calls:
            raise RuntimeBudgetExhausted("model-call")
        self.model_calls += 1

    def consume_tool(self) -> None:
        self.check_deadline()
        if self.tool_calls >= self.limits.max_tool_calls:
            raise RuntimeBudgetExhausted("tool-call")
        self.tool_calls += 1

    def consume_repair(self) -> None:
        self.check_deadline()
        if self.repairs >= self.limits.max_repairs_per_stage:
            raise RuntimeBudgetExhausted("repair")
        self.repairs += 1

    @property
    def elapsed_ms(self) -> int:
        return max(0, round((monotonic() - self.started_clock) * 1000))

    @property
    def remaining_seconds(self) -> float:
        """Return the exact wall-clock time left before the run deadline."""

        now = monotonic()
        profile_deadline = self.started_clock + (self.limits.target_latency_ms / 1000)
        deadline = (
            min(profile_deadline, self.deadline_clock)
            if self.deadline_clock is not None
            else profile_deadline
        )
        return max(0.0, deadline - now)


def new_budget(profile: BudgetProfileName) -> RuntimeBudgetCounter:
    return RuntimeBudgetCounter(budget_limits_for(profile))
