from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable, Coroutine
from pathlib import Path
from traceback import walk_tb
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import execution_session_scope
from app.modules.agents.repository import AgentRunRepository

logger = logging.getLogger(__name__)


def _safe_exception_trace(error: BaseException) -> list[dict[str, object]]:
    """Return bounded code locations, never messages, source lines or locals."""
    chain: list[dict[str, object]] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and len(chain) < 3 and id(current) not in seen:
        seen.add(id(current))
        frames = deque(maxlen=12)
        if current.__traceback__ is not None:
            for frame, line in walk_tb(current.__traceback__):
                frames.append(
                    {
                        "file": Path(frame.f_code.co_filename).name,
                        "line": line,
                        "function": frame.f_code.co_name,
                    }
                )
        chain.append({"error_type": type(current).__name__, "frames": list(frames)})
        current = current.__cause__ or current.__context__
    return chain


class RecoverableAgentService(Protocol):
    async def execute_resumed(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        claim_id: UUID | None = None,
    ) -> None: ...

    async def fail_orphaned_execution(
        self,
        *,
        company_id: UUID,
        run_id: UUID,
        claim_id: UUID,
    ) -> None: ...


type RecoverableAgentServiceFactory = Callable[[AsyncSession], RecoverableAgentService]


class AgentTaskRegistry:
    """Own in-process agent tasks so exceptions and shutdown are handled safely."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_on_shutdown: set[str] = set()

    def spawn(
        self,
        coroutine: Coroutine[Any, Any, None],
        *,
        name: str,
        cancel_on_shutdown: bool = False,
    ) -> None:
        existing = self._tasks.get(name)
        if existing is not None and not existing.done():
            coroutine.close()
            return
        task = asyncio.create_task(coroutine, name=name)
        self._tasks[name] = task
        if cancel_on_shutdown:
            self._cancel_on_shutdown.add(name)
        else:
            self._cancel_on_shutdown.discard(name)
        task.add_done_callback(lambda completed: self._discard(name, completed))

    def _discard(self, name: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(name) is task:
            self._tasks.pop(name, None)
            self._cancel_on_shutdown.discard(name)
        if task.cancelled():
            return
        # Retrieving the exception prevents an unobserved-task warning. Log only
        # types and code locations; provider/database messages may contain secrets.
        error = task.exception()
        if error is not None:
            logger.error(
                "Background agent task %s failed with %s. Code locations: %s",
                name,
                type(error).__name__,
                _safe_exception_trace(error),
            )

    async def shutdown(self, *, timeout_seconds: float = 20.0) -> None:
        tasks = tuple(self._tasks.values())
        if not tasks:
            return
        for name in tuple(self._cancel_on_shutdown):
            task = self._tasks.get(name)
            if task is not None and not task.done():
                task.cancel()
        _, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._cancel_on_shutdown.clear()


async def recover_orphaned_agent_runs(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    service_factory: RecoverableAgentServiceFactory,
    max_runs: int = 25,
    lease_seconds: int = 90,
) -> int:
    """Run one bounded recovery pass for expired agent executions."""

    if not 1 <= max_runs <= 100:
        raise ValueError("max_runs must be between 1 and 100")
    recovered = 0
    for _ in range(max_runs):
        async with session_factory() as claim_session:
            repository = AgentRunRepository(claim_session)
            candidate = await repository.claim_next_recoverable(
                lease_seconds=lease_seconds,
            )
            await repository.commit()
        if candidate is None:
            break
        async with execution_session_scope(session_factory) as execution_session:
            try:
                service = service_factory(execution_session)
                if candidate.mode == "fail_closed":
                    await service.fail_orphaned_execution(
                        company_id=candidate.company_id,
                        run_id=candidate.run_id,
                        claim_id=candidate.claim.claim_id,
                    )
                else:
                    await service.execute_resumed(
                        company_id=candidate.company_id,
                        run_id=candidate.run_id,
                        claim_id=candidate.claim.claim_id,
                    )
            except Exception as error:  # noqa: BLE001 - lease expiry permits a later safe recovery
                await execution_session.rollback()
                logger.error(
                    "Recovered agent run %s failed with a safe background error. Code locations: %s",
                    candidate.run_id,
                    _safe_exception_trace(error),
                )
        recovered += 1
    return recovered


async def sweep_orphaned_agent_runs(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    service_factory: RecoverableAgentServiceFactory,
    stop_event: asyncio.Event,
    interval_seconds: float = 30.0,
    max_runs_per_sweep: int = 25,
    lease_seconds: int = 90,
) -> None:
    """Periodically reclaim expired executions until application shutdown.

    Each pass is bounded, and failures are isolated to that pass so a temporary
    database outage does not permanently disable orphan recovery on a healthy
    worker. ``stop_event`` makes the sleep cancellation-safe and lets lifespan
    shutdown complete without waiting for the full interval.
    """

    if not 0 < interval_seconds <= 3600:
        raise ValueError("interval_seconds must be between 0 and 3600")
    while not stop_event.is_set():
        try:
            await recover_orphaned_agent_runs(
                session_factory=session_factory,
                service_factory=service_factory,
                max_runs=max_runs_per_sweep,
                lease_seconds=lease_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - the next sweep retries safely
            logger.warning(
                "Agent orphan recovery sweep was deferred after %s.",
                type(error).__name__,
            )
        if stop_event.is_set():
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


agent_task_registry = AgentTaskRegistry()
