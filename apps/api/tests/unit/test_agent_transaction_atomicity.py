from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agents.budget import RuntimeBudgetCounter
from app.modules.agents.graph_adapter import RegistryGraphToolInvoker
from app.modules.agents.graph_contracts import (
    BudgetLimits,
    ContextEnvelope,
    ToolInvocation,
)
from app.modules.agents.repository import DurableToolInvocation
from app.modules.agents.runtime import _RuntimeBudgetInvoker
from app.modules.agents.step_recorder import AgentStepRecorder
from app.modules.agents.tools import AgentToolRegistry


class _TransactionalRepository:
    """Small transaction model used to expose accidental failure-path commits."""

    def __init__(self) -> None:
        self.staged_domain_rows: list[str] = []
        self.committed_domain_rows: list[str] = []
        self.staged_journal_events: list[str] = []
        self.committed_journal_events: list[str] = []
        self.operations: list[str] = []
        self.rollback_count = 0
        self.refresh_count = 0

    async def reserve_tool_invocation(self, **kwargs) -> DurableToolInvocation:
        del kwargs
        self.operations.append("journal:tool.started")
        self.staged_journal_events.append("tool.started")
        return DurableToolInvocation(reserved=True)

    async def append_step(self, **kwargs):
        event_name = kwargs["output_snapshot"]["event_name"]
        self.operations.append(f"journal:{event_name}")
        self.staged_journal_events.append(event_name)
        return SimpleNamespace(sequence=len(self.operations))

    async def commit(self) -> None:
        self.operations.append("commit")
        self.committed_domain_rows.extend(self.staged_domain_rows)
        self.committed_journal_events.extend(self.staged_journal_events)
        self.staged_domain_rows.clear()
        self.staged_journal_events.clear()

    async def rollback(self) -> None:
        self.operations.append("rollback")
        self.rollback_count += 1
        self.staged_domain_rows.clear()
        self.staged_journal_events.clear()

    async def get(self, **kwargs):
        del kwargs
        self.operations.append("refresh")
        self.refresh_count += 1
        return SimpleNamespace(id=uuid4())


class _ServicePort:
    def __init__(self, handler) -> None:
        self._handler = handler

    def handler_for(self, tool_name):
        del tool_name
        return self._handler


def _invocation() -> ToolInvocation:
    return ToolInvocation(
        run_id=uuid4(),
        graph_name="orchestrator",
        module=None,
        stage_id="context.resolve",
        tool_name="resolve_context",
        context=ContextEnvelope(
            company_id=uuid4(),
            actor_id=uuid4(),
            actor_role="sustainability_analyst",
            modules=("measurement",),
            request_hash="1" * 64,
            analysis_signature="2" * 64,
        ),
        checkpoint_revision=0,
    )


def _recorder(repository: _TransactionalRepository, invocation: ToolInvocation):
    return AgentStepRecorder(
        repository,  # type: ignore[arg-type]
        company_id=invocation.context.company_id,
        run_id=invocation.run_id,
    )


@pytest.mark.asyncio
async def test_handler_exception_rolls_back_staged_domain_row_before_failure_commit() -> None:
    repository = _TransactionalRepository()
    invocation = _invocation()
    recorder = _recorder(repository, invocation)

    async def failing_handler(*, context, arguments):
        del context, arguments
        repository.operations.append("stage:domain-row")
        repository.staged_domain_rows.append("partial-domain-row")
        raise RuntimeError("unsafe internal detail")

    invoker = RegistryGraphToolInvoker(
        AgentToolRegistry(_ServicePort(failing_handler)),  # type: ignore[arg-type]
        payload_factory=lambda _: {},
        recorder=recorder,
    )

    result = await invoker.invoke(invocation)

    assert result.status == "validation_failed"
    assert result.code == "tool_validation_failed"
    assert repository.committed_domain_rows == []
    assert repository.committed_journal_events == ["tool.started", "tool.failed"]
    assert repository.rollback_count == 1
    assert repository.refresh_count == 1
    assert repository.operations == [
        "journal:tool.started",
        "commit",
        "stage:domain-row",
        "rollback",
        "refresh",
        "journal:tool.failed",
        "commit",
    ]


@pytest.mark.asyncio
async def test_timeout_cancellation_recovers_before_later_checkpoint_commit() -> None:
    repository = _TransactionalRepository()
    invocation = _invocation()
    recorder = _recorder(repository, invocation)
    cancelled = asyncio.Event()

    async def slow_handler(*, context, arguments):
        del context, arguments
        repository.operations.append("stage:domain-row")
        repository.staged_domain_rows.append("partial-domain-row")
        try:
            await asyncio.Event().wait()
        finally:
            repository.operations.append("handler:cancelled")
            cancelled.set()

    budget = RuntimeBudgetCounter(
        BudgetLimits(
            max_model_calls=1,
            max_tool_calls=1,
            max_repairs_per_stage=0,
            target_latency_ms=25,
        )
    )
    invoker = _RuntimeBudgetInvoker(
        RegistryGraphToolInvoker(
            AgentToolRegistry(_ServicePort(slow_handler)),  # type: ignore[arg-type]
            payload_factory=lambda _: {},
            recorder=recorder,
        ),
        budget,
    )

    result = await invoker.invoke(invocation)
    # This is the checkpoint/terminal write that follows the typed budget result
    # in the durable graph runner. It must not be able to flush the tool's row.
    await recorder.record(
        step_type="node",
        event_name="node.failed",
        status="blocked",
        graph_name="orchestrator",
        node_name="orchestrator.context.resolve",
        error_code=result.code,
    )

    assert result.status == "budget_exhausted"
    assert result.code == "latency_budget_exhausted"
    assert cancelled.is_set()
    assert repository.committed_domain_rows == []
    assert repository.committed_journal_events == ["tool.started", "node.failed"]
    assert repository.rollback_count == 1
    assert repository.refresh_count == 1
    assert repository.operations == [
        "journal:tool.started",
        "commit",
        "stage:domain-row",
        "handler:cancelled",
        "rollback",
        "refresh",
        "journal:node.failed",
        "commit",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellation_source", ["deadline", "shutdown"])
async def test_cancelled_tool_recovers_under_lock_before_queued_checkpoint(
    cancellation_source: str,
) -> None:
    persistence_lock = asyncio.Lock()
    handler_started = asyncio.Event()
    recovery_started = asyncio.Event()
    release_recovery = asyncio.Event()
    checkpoint_waiting = asyncio.Event()
    checkpoint_entered = asyncio.Event()

    class DelayedRollbackRepository(_TransactionalRepository):
        async def rollback(self) -> None:
            assert persistence_lock.locked()
            recovery_started.set()
            await release_recovery.wait()
            await super().rollback()

    repository = DelayedRollbackRepository()
    invocation = _invocation()
    recorder = _recorder(repository, invocation)

    async def slow_handler(*, context, arguments):
        del context, arguments
        repository.staged_domain_rows.append("partial-domain-row")
        handler_started.set()
        await asyncio.Event().wait()

    invoker = _RuntimeBudgetInvoker(
        RegistryGraphToolInvoker(
            AgentToolRegistry(_ServicePort(slow_handler)),  # type: ignore[arg-type]
            payload_factory=lambda _: {},
            recorder=recorder,
            persistence_lock=persistence_lock,
        ),
        RuntimeBudgetCounter(
            BudgetLimits(
                max_model_calls=1,
                max_tool_calls=1,
                max_repairs_per_stage=0,
                target_latency_ms=100 if cancellation_source == "deadline" else 5000,
            )
        ),
    )

    async def checkpoint() -> None:
        checkpoint_waiting.set()
        async with persistence_lock:
            checkpoint_entered.set()
            await recorder.record(
                step_type="node",
                event_name="node.completed",
                status="completed",
                graph_name="orchestrator",
                node_name="orchestrator.checkpoint",
            )

    async with asyncio.timeout(2):
        tool_task = asyncio.create_task(invoker.invoke(invocation))
        await handler_started.wait()
        checkpoint_task = asyncio.create_task(checkpoint())
        await checkpoint_waiting.wait()
        if cancellation_source == "shutdown":
            tool_task.cancel()
        await recovery_started.wait()
        assert persistence_lock.locked()
        assert not checkpoint_entered.is_set()
        release_recovery.set()
        if cancellation_source == "shutdown":
            with pytest.raises(asyncio.CancelledError):
                await tool_task
        else:
            result = await tool_task
            assert result.status == "budget_exhausted"
        await checkpoint_task

    assert repository.committed_domain_rows == []
    assert repository.committed_journal_events == ["tool.started", "node.completed"]
    assert repository.rollback_count == repository.refresh_count == 1
    assert repository.operations.index("rollback") < repository.operations.index(
        "journal:node.completed"
    )


@pytest.mark.asyncio
async def test_cancellation_while_waiting_for_session_lock_does_not_rollback_its_owner() -> None:
    repository = _TransactionalRepository()
    invocation = _invocation()
    persistence_lock = asyncio.Lock()
    recorder = _recorder(repository, invocation)
    task_started = asyncio.Event()
    invoker = RegistryGraphToolInvoker(
        AgentToolRegistry(_ServicePort(None)),  # type: ignore[arg-type]
        recorder=recorder,
        persistence_lock=persistence_lock,
    )

    async def invoke() -> None:
        task_started.set()
        await invoker.invoke(invocation)

    async with persistence_lock:
        task = asyncio.create_task(invoke())
        await task_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert repository.operations == []
