"""Sequence allocation under contending, committing and rolling-back writers."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text

from app.db.models.ai import AgentRun, AgentRunStep
from app.modules.agents.repository import AgentRunRepository, AgentRunStepParentNotFoundError


async def _create_parent(context):
    run_id = uuid4()
    async with context.session_factory() as session:
        session.add(
            AgentRun(
                id=run_id,
                company_id=context.ids.company_id,
                actor_id=context.ids.analyst_id,
                trace_id=f"synthetic-sequence-{run_id}",
                workflow="measurement",
                stage="graph",
                terminal_state="running",
                context_envelope={},
                telemetry={},
            )
        )
        await session.commit()
    return run_id


async def _append(session, *, company_id, run_id, node):
    return await AgentRunRepository(session).append_step(
        company_id=company_id,
        run_id=run_id,
        step_type="node",
        status="running",
        graph_name="measurement",
        node_name=node,
        output_snapshot={"event_name": "node.started", "data": {"synthetic": True}},
    )


async def _wait_for_lock(engine, pid: int) -> None:
    # Verify server-side contention, rather than assuming a scheduling sleep is
    # sufficient to start B's statement before A releases its lock.
    async with asyncio.timeout(5), engine.connect() as observer:
        while True:
            waiting = await observer.scalar(
                text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
            await observer.rollback()
            if waiting:
                return
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("first_commits", [True, False])
async def test_step_allocation_uses_snapshot_after_parent_lock(
    e2e_context, first_commits: bool
) -> None:
    run_id = await _create_parent(e2e_context)
    company_id = e2e_context.ids.company_id
    async with e2e_context.session_factory() as first, e2e_context.session_factory() as second:
        first_step = await _append(first, company_id=company_id, run_id=run_id, node="first")
        assert first_step.sequence == 1
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))

        async def append_second():
            step = await _append(second, company_id=company_id, run_id=run_id, node="second")
            sequence = step.sequence
            await second.commit()
            return sequence

        contender = asyncio.create_task(append_second())
        try:
            await _wait_for_lock(e2e_context.engine, second_pid)
            assert contender.done() is False
            if first_commits:
                await first.commit()
            else:
                await first.rollback()
            second_sequence = await asyncio.wait_for(contender, timeout=5)
        finally:
            if not contender.done():
                contender.cancel()
            await asyncio.gather(contender, return_exceptions=True)

    assert second_sequence == (2 if first_commits else 1)
    async with e2e_context.session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(AgentRunStep.sequence, AgentRunStep.node_name)
                    .where(
                        AgentRunStep.company_id == company_id,
                        AgentRunStep.agent_run_id == run_id,
                    )
                    .order_by(AgentRunStep.sequence)
                )
            ).all()
        )
    assert rows == ([(1, "first"), (2, "second")] if first_commits else [(1, "second")])


@pytest.mark.asyncio
async def test_append_uses_lock_and_insert_returning_without_extra_max_query(e2e_context) -> None:
    run_id = await _create_parent(e2e_context)
    statements = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(e2e_context.engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        async with e2e_context.session_factory() as session:
            step = await _append(
                session,
                company_id=e2e_context.ids.company_id,
                run_id=run_id,
                node="single",
            )
            assert step.sequence == 1
            assert step.id is not None
            await session.commit()
    finally:
        event.remove(e2e_context.engine.sync_engine, "before_cursor_execute", record_statement)

    assert len(statements) == 2
    assert "FOR UPDATE" in statements[0]
    assert statements[1].startswith("INSERT INTO ai.agent_run_steps")
    assert "max(ai.agent_run_steps.sequence)" in statements[1]
    assert "RETURNING" in statements[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_tenant", [True, False])
async def test_step_allocation_rejects_missing_or_cross_tenant_parent(
    e2e_context, wrong_tenant: bool
) -> None:
    real_run_id = await _create_parent(e2e_context)
    async with e2e_context.session_factory() as session:
        with pytest.raises(AgentRunStepParentNotFoundError):
            await _append(
                session,
                company_id=uuid4() if wrong_tenant else e2e_context.ids.company_id,
                run_id=real_run_id if wrong_tenant else uuid4(),
                node="forbidden",
            )
        await session.rollback()
        assert list(
            await session.scalars(
                select(AgentRunStep).where(AgentRunStep.agent_run_id == real_run_id)
            )
        ) == []
