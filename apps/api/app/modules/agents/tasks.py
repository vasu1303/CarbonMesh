from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


class AgentTaskRegistry:
    """Own in-process agent tasks so exceptions and shutdown are handled safely."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def spawn(self, coroutine: Coroutine[Any, Any, None], *, name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._discard)

    def _discard(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        # Retrieving the exception prevents an unobserved-task warning. The worker
        # persists safe failures before returning whenever the database is available.
        task.exception()

    async def shutdown(self, *, timeout_seconds: float = 20.0) -> None:
        tasks = tuple(self._tasks)
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.difference_update(tasks)


agent_task_registry = AgentTaskRegistry()
