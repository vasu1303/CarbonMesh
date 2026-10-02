from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.db.models.ai import AgentRun, AgentRunStep
from app.db.models.core import Company
from app.db.session import execution_session_scope
from app.modules.agents.graph_adapter import RegistryGraphToolInvoker
from app.modules.agents.graph_contracts import (
    ContextEnvelope,
    ExecutionPlan,
    GraphState,
    PlanStage,
    ToolInvocation,
    ToolResult,
    UnsupportedItem,
)
from app.modules.agents.repository import AgentRunRepository
from app.modules.agents.resume import ApprovalResumeState
from app.modules.agents.runtime import GraphAgentRunService
from app.modules.agents.schemas import AgentExecutionLease, AgentTelemetry
from app.modules.agents.step_recorder import AgentStepRecorder
from app.modules.agents.tasks import (
    recover_orphaned_agent_runs,
    sweep_orphaned_agent_runs,
)
from app.modules.agents.tools import AgentToolRegistry


def _telemetry(*, lease: AgentExecutionLease | None = None) -> dict[str, object]:
    return AgentTelemetry(
        trace_id=f"durability-{uuid4()}",
        analysis_signature="a" * 64,
        elapsed_ms=0,
        graph_state={"durable": True},
        checkpoint={"revision": 1},
        execution_attempts=lease.attempt if lease is not None else 0,
        execution_lease=lease,
    ).model_dump(mode="json")


async def _insert_running_graph(e2e_context, *, lease=None, started_at=None) -> UUID:
    run_id = uuid4()
    async with e2e_context.session_factory() as session:
        session.add(
            AgentRun(
                id=run_id,
                company_id=e2e_context.ids.company_id,
                actor_id=e2e_context.ids.analyst_id,
                trace_id=f"durability-{run_id}",
                workflow="measurement",
                stage="graph",
                terminal_state="running",
                context_envelope={"analysis_signature": "a" * 64},
                context_hash="a" * 64,
                telemetry=_telemetry(lease=lease),
                **({"started_at": started_at} if started_at is not None else {}),
            )
        )
        await session.commit()
    return run_id


@pytest.mark.asyncio
async def test_cancelled_tool_rolls_back_before_queued_database_checkpoint(e2e_context) -> None:
    run_id = await _insert_running_graph(e2e_context)
    partial_company_id = uuid4()
    handler_started = asyncio.Event()
    checkpoint_waiting = asyncio.Event()
    persistence_lock = asyncio.Lock()

    async with execution_session_scope(e2e_context.session_factory) as session:
        repository = AgentRunRepository(session)
        recorder = AgentStepRecorder(
            repository,
            company_id=e2e_context.ids.company_id,
            run_id=run_id,
        )

        async def partial_handler(*, context, arguments):
            del context, arguments
            session.add(
                Company(
                    id=partial_company_id,
                    code=f"cancelled-{partial_company_id.hex}",
                    name="Synthetic uncommitted cancellation probe",
                    is_synthetic=True,
                )
            )
            await session.flush()
            handler_started.set()
            await asyncio.Event().wait()

        class ServicePort:
            def handler_for(self, _name):
                return partial_handler

        invoker = RegistryGraphToolInvoker(
            AgentToolRegistry(ServicePort()),  # type: ignore[arg-type]
            payload_factory=lambda _: {},
            recorder=recorder,
            persistence_lock=persistence_lock,
        )
        invocation = ToolInvocation(
            run_id=run_id,
            graph_name="orchestrator",
            module=None,
            stage_id="context.resolve",
            tool_name="resolve_context",
            context=ContextEnvelope(
                company_id=e2e_context.ids.company_id,
                actor_id=e2e_context.ids.analyst_id,
                actor_role="sustainability_analyst",
                modules=("measurement",),
                request_hash="1" * 64,
                analysis_signature="a" * 64,
            ),
            checkpoint_revision=0,
        )

        async def persist_checkpoint() -> None:
            checkpoint_waiting.set()
            async with persistence_lock:
                await recorder.record(
                    step_type="node",
                    event_name="node.completed",
                    status="completed",
                    graph_name="orchestrator",
                    node_name="orchestrator.checkpoint",
                )

        async with asyncio.timeout(10):
            tool_task = asyncio.create_task(invoker.invoke(invocation))
            await handler_started.wait()
            checkpoint_task = asyncio.create_task(persist_checkpoint())
            await checkpoint_waiting.wait()
            tool_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await tool_task
            await checkpoint_task

    async with e2e_context.session_factory() as session:
        assert await session.get(Company, partial_company_id) is None
        steps = list(
            await session.scalars(
                select(AgentRunStep)
                .where(AgentRunStep.agent_run_id == run_id)
                .order_by(AgentRunStep.sequence)
            )
        )
        assert [step.sequence for step in steps] == [1, 2]
        assert [step.output_snapshot["event_name"] for step in steps] == [
            "tool.started",
            "node.completed",
        ]


@pytest.mark.asyncio
async def test_resume_approval_database_timeout_persists_typed_budget_stop(e2e_context) -> None:
    run_id = await _insert_running_graph(e2e_context)
    plan = ExecutionPlan(
        profile="golden",
        stages=(
            PlanStage(stage_id="measurement.run", module="measurement"),
            PlanStage(
                stage_id="procurement.run", module="procurement", depends_on=("measurement",)
            ),
        ),
    )
    state = GraphState.initial(
        run_id=run_id,
        context=ContextEnvelope(
            company_id=e2e_context.ids.company_id,
            actor_id=e2e_context.ids.analyst_id,
            actor_role="sustainability_analyst",
            modules=plan.modules,
            request_hash="1" * 64,
            analysis_signature="a" * 64,
        ),
        plan=plan,
    )
    async with e2e_context.session_factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.stage = "resumed_approval"
        run.latency_ms = 44_032
        run.telemetry = AgentTelemetry(
            trace_id=run.trace_id,
            analysis_signature="a" * 64,
            elapsed_ms=44_032,
            graph_state=state.model_dump(mode="json"),
            checkpoint=state.checkpoint.model_dump(mode="json"),
            resume_kind="approval",
            resume_approval={
                "resumed_by": str(e2e_context.ids.approver_id),
                "approval_id": str(uuid4()),
                "preview_hash": "b" * 64,
                "analysis_signature": "a" * 64,
                "context_hash": "a" * 64,
                "target_type": "procurement_recommendation",
                "target_id": str(uuid4()),
                "resume_segment_elapsed_ms": 3516,
            },
        ).model_dump(mode="json")
        await session.commit()

    validation_started = asyncio.Event()
    async with execution_session_scope(e2e_context.session_factory) as session:
        class DatabaseApprovalObserver:
            async def validate(self, **_kwargs) -> ApprovalResumeState:
                validation_started.set()
                # Exercise real asyncpg cancellation/invalidation, not a sleep
                # in application code or a mocked session/budget clock.
                await session.execute(text("SELECT pg_sleep(5)"))
                raise AssertionError("approval query should be cancelled")

        service = GraphAgentRunService(
            AgentRunRepository(session),
            approval_resume=DatabaseApprovalObserver(),
        )
        await service.execute_resumed(company_id=e2e_context.ids.company_id, run_id=run_id)

    assert validation_started.is_set()
    async with e2e_context.session_factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        assert run.terminal_state == "budget_exhausted"
        assert run.error_code == "latency_budget_exhausted"
        assert run.tool_calls == 0
        steps = list(
            await session.scalars(
                select(AgentRunStep)
                .where(AgentRunStep.agent_run_id == run_id)
                .order_by(AgentRunStep.sequence)
            )
        )
        assert len(steps) == 1
        assert steps[0].output_snapshot["event_name"] == "run.stopped"
        assert steps[0].error_code == "latency_budget_exhausted"


@pytest.mark.asyncio
async def test_only_one_worker_can_claim_the_same_running_agent_run(e2e_context) -> None:
    run_id = await _insert_running_graph(e2e_context)

    async def claim():
        async with e2e_context.session_factory() as session:
            repository = AgentRunRepository(session)
            observed = await repository.try_claim_execution(
                company_id=e2e_context.ids.company_id,
                run_id=run_id,
                lease_seconds=90,
            )
            await repository.commit()
            return observed

    claims = await asyncio.gather(claim(), claim())

    assert sum(item is not None for item in claims) == 1
    winning_claim = next(item for item in claims if item is not None)
    assert winning_claim.attempt == 1
    assert winning_claim.recovered is False


@pytest.mark.asyncio
async def test_expired_checkpointed_run_is_recovered_once_across_workers(
    e2e_context,
) -> None:
    now = datetime.now(UTC)
    expired = AgentExecutionLease(
        claim_id=uuid4(),
        claimed_at=now - timedelta(minutes=3),
        expires_at=now - timedelta(minutes=2),
        attempt=1,
    )
    run_id = await _insert_running_graph(e2e_context, lease=expired)
    calls: list[tuple[UUID, UUID, UUID]] = []

    class RecoveryService:
        async def execute_resumed(
            self,
            *,
            company_id: UUID,
            run_id: UUID,
            claim_id: UUID,
        ) -> None:
            calls.append((company_id, run_id, claim_id))

    recovered = await asyncio.gather(
        recover_orphaned_agent_runs(
            session_factory=e2e_context.session_factory,
            service_factory=lambda _: RecoveryService(),
            max_runs=1,
        ),
        recover_orphaned_agent_runs(
            session_factory=e2e_context.session_factory,
            service_factory=lambda _: RecoveryService(),
            max_runs=1,
        ),
    )

    assert sum(recovered) == 1
    assert len(calls) == 1
    assert calls[0][:2] == (e2e_context.ids.company_id, run_id)
    async with e2e_context.session_factory() as session:
        run = await AgentRunRepository(session).get(
            company_id=e2e_context.ids.company_id,
            run_id=run_id,
        )
        assert run is not None
        lease = AgentTelemetry.model_validate(run.telemetry).execution_lease
        assert lease is not None
        assert lease.claim_id == calls[0][2]
        assert lease.attempt == 2
        assert lease.recovered is True


@pytest.mark.asyncio
async def test_periodic_sweeper_recovers_orphan_created_after_startup(
    e2e_context,
) -> None:
    stop_event = asyncio.Event()
    recovered_event = asyncio.Event()
    calls: list[tuple[UUID, UUID, UUID, datetime]] = []

    class RecoveryService:
        async def execute_resumed(
            self,
            *,
            company_id: UUID,
            run_id: UUID,
            claim_id: UUID,
        ) -> None:
            calls.append((company_id, run_id, claim_id, datetime.now(UTC)))
            recovered_event.set()

    sweeper = asyncio.create_task(
        sweep_orphaned_agent_runs(
            session_factory=e2e_context.session_factory,
            service_factory=lambda _: RecoveryService(),
            stop_event=stop_event,
            interval_seconds=0.05,
            max_runs_per_sweep=1,
            lease_seconds=1,
        )
    )
    try:
        # Let the initial empty pass complete, then create a run whose current
        # owner disappears. A startup-only recovery task would never see it.
        await asyncio.sleep(0.1)
        now = datetime.now(UTC)
        lease = AgentExecutionLease(
            claim_id=uuid4(),
            claimed_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(seconds=2),
            attempt=1,
        )
        run_id = await _insert_running_graph(e2e_context, lease=lease)

        await asyncio.sleep(0.08)
        assert calls == []
        await asyncio.wait_for(recovered_event.wait(), timeout=5)
    finally:
        stop_event.set()
        await asyncio.wait_for(sweeper, timeout=2)

    assert len(calls) == 1
    assert calls[0][:2] == (e2e_context.ids.company_id, run_id)
    assert calls[0][3] >= lease.expires_at


@pytest.mark.asyncio
async def test_recovery_scans_past_a_full_page_of_active_leases(e2e_context) -> None:
    now = datetime.now(UTC)
    for index in range(25):
        active = AgentExecutionLease(
            claim_id=uuid4(),
            claimed_at=now - timedelta(minutes=10),
            expires_at=now + timedelta(minutes=10),
            attempt=1,
        )
        await _insert_running_graph(
            e2e_context,
            lease=active,
            started_at=now - timedelta(minutes=20) + timedelta(microseconds=index),
        )

    expired = AgentExecutionLease(
        claim_id=uuid4(),
        claimed_at=now - timedelta(minutes=4),
        expires_at=now - timedelta(minutes=3),
        attempt=1,
    )
    orphan_id = await _insert_running_graph(
        e2e_context,
        lease=expired,
        started_at=now - timedelta(minutes=5),
    )

    async with e2e_context.session_factory() as session:
        repository = AgentRunRepository(session)
        recovered = await repository.claim_next_recoverable(
            lease_seconds=90,
            scan_limit=25,
            now=now,
        )
        await repository.commit()

    assert recovered is not None
    assert recovered.run_id == orphan_id
    assert recovered.mode == "checkpoint"
    assert recovered.claim.recovered is True


@pytest.mark.asyncio
async def test_tool_invocation_journal_reserves_once_and_replays_durable_result(
    e2e_context,
) -> None:
    run_id = await _insert_running_graph(e2e_context)
    invocation_key = "b" * 64

    async def reserve():
        async with e2e_context.session_factory() as session:
            return await AgentStepRecorder(
                AgentRunRepository(session),
                company_id=e2e_context.ids.company_id,
                run_id=run_id,
            ).reserve_tool_invocation(
                invocation_key=invocation_key,
                step_type="tool",
                graph_name="measurement",
                node_name="measurement.calculate_emissions",
                tool_name="calculate_emissions",
                input_summary={"invocation_key": invocation_key},
                data={"tool_name": "calculate_emissions"},
            )

    reservations = await asyncio.gather(reserve(), reserve())

    assert sum(item.reserved for item in reservations) == 1
    assert all(item.result is None for item in reservations)

    source_id = uuid4()
    durable_result = ToolResult(
        status="unsupported",
        unsupported_items=(
            UnsupportedItem(
                code="evidence_gap",
                reason="The cited evidence does not support this atomic claim.",
                module="assurance",
                source_ids=(source_id,),
            ),
        ),
        rows_processed=1,
        code="evidence_gap",
    )
    async with e2e_context.session_factory() as session:
        repository = AgentRunRepository(session)
        recorder = AgentStepRecorder(
            repository,
            company_id=e2e_context.ids.company_id,
            run_id=run_id,
        )
        await recorder.record(
            step_type="tool",
            event_name="tool.completed",
            status="completed",
            graph_name="measurement",
            node_name="measurement.calculate_emissions",
            tool_name="calculate_emissions",
            input_summary={"invocation_key": invocation_key},
            data={"durable_result": durable_result.model_dump(mode="json")},
        )
        # Fact telemetry shares the same graph/node/tool attribution but not the
        # invocation key. More than one old query page must not bury the exact
        # completed result and cause the domain handler to execute again.
        for index in range(15):
            await recorder.record(
                step_type="validation",
                event_name="fact.created",
                status="completed",
                graph_name="measurement",
                node_name="measurement.calculate_emissions",
                tool_name="calculate_emissions",
                data={"fact_id": str(uuid4()), "index": index},
                commit=False,
            )
        await repository.commit()

    replay = await reserve()

    assert replay.reserved is False
    assert replay.result == durable_result
    assert replay.result.unsupported_items[0].source_ids == (source_id,)


@pytest.mark.asyncio
async def test_old_queued_orphan_without_checkpoint_is_stopped_fail_closed(
    e2e_context,
) -> None:
    run_id = uuid4()
    async with e2e_context.session_factory() as session:
        session.add(
            AgentRun(
                id=run_id,
                company_id=e2e_context.ids.company_id,
                actor_id=e2e_context.ids.analyst_id,
                trace_id=f"uncheckpointed-{run_id}",
                workflow="planning",
                stage="queued",
                terminal_state="running",
                context_envelope={"analysis_signature": "a" * 64},
                context_hash="a" * 64,
                telemetry=AgentTelemetry(
                    trace_id=f"uncheckpointed-{run_id}",
                    analysis_signature="a" * 64,
                    elapsed_ms=0,
                ).model_dump(mode="json"),
                started_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        await session.commit()

    recovered = await recover_orphaned_agent_runs(
        session_factory=e2e_context.session_factory,
        service_factory=lambda session: GraphAgentRunService(
            AgentRunRepository(session)
        ),
        max_runs=1,
    )

    assert recovered == 1
    async with e2e_context.session_factory() as session:
        run = await AgentRunRepository(session).get(
            company_id=e2e_context.ids.company_id,
            run_id=run_id,
        )
        assert run is not None
        assert run.terminal_state == "validation_failed"
        assert run.error_code == "orphan_checkpoint_unavailable"
        assert AgentTelemetry.model_validate(run.telemetry).execution_lease is None
