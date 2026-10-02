from __future__ import annotations

import pytest

from app.modules.agents.tasks import AgentTaskRegistry, _safe_exception_trace


@pytest.mark.asyncio
async def test_background_failure_logs_code_locations_without_sensitive_values(caplog) -> None:
    async def synthetic_failure():
        private_local = "synthetic-private-key-must-not-appear"
        try:
            raise ValueError(private_local)
        except ValueError as error:
            raise RuntimeError("synthetic-database-password-must-not-appear") from error

    registry = AgentTaskRegistry()
    registry.spawn(synthetic_failure(), name="synthetic-task")
    await registry.shutdown(timeout_seconds=2)

    assert "RuntimeError" in caplog.text
    assert "ValueError" in caplog.text
    assert "synthetic_failure" in caplog.text
    assert "test_agent_task_diagnostics.py" in caplog.text
    assert "synthetic-private-key-must-not-appear" not in caplog.text
    assert "synthetic-database-password-must-not-appear" not in caplog.text
    assert "private_local" not in caplog.text
    assert "C:\\Users" not in caplog.text


def test_safe_trace_bounds_exception_chains_and_cycles() -> None:
    first = ValueError("synthetic secret")
    second = RuntimeError("synthetic secret")
    first.__cause__ = second
    second.__cause__ = first

    trace = _safe_exception_trace(first)

    assert len(trace) == 2
    assert trace == [
        {"error_type": "ValueError", "frames": []},
        {"error_type": "RuntimeError", "frames": []},
    ]
