from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes import agents as agent_routes
from app.modules.agents.resume import GenericApprovalResumeObserver
from app.modules.agents.runtime import AgentResumeError
from app.modules.agents.schemas import (
    AgentResumeRequest,
    AgentResumeResult,
    AgentSustainabilityMetrics,
    SustainabilityAssumptions,
)


def test_resume_and_sustainability_routes_are_registered() -> None:
    registered = {
        (method, route.path)
        for route in agent_routes.router.routes
        for method in route.methods or set()
    }

    assert ("POST", "/runs/{run_id}/resume") in registered
    assert ("GET", "/metrics/agent-sustainability") in registered


def test_production_approval_observation_uses_shared_service() -> None:
    observer = agent_routes._approval_resume_port(object())  # type: ignore[arg-type]
    assert isinstance(observer, GenericApprovalResumeObserver)


class FakeSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


def _resume_request() -> AgentResumeRequest:
    return AgentResumeRequest(
        company_id=uuid4(),
        actor_id=uuid4(),
        interrupt_id=uuid4(),
        interrupt_sequence=4,
        analysis_signature="a" * 64,
        idempotency_key="resume-key-001",
        approval={
            "approval_id": uuid4(),
            "preview_hash": "b" * 64,
        },
    )


@pytest.mark.asyncio
async def test_resume_route_spawns_only_an_accepted_running_continuation(monkeypatch) -> None:
    request = _resume_request()
    run_id = uuid4()
    session = FakeSession()
    spawned: list[tuple[str, object]] = []

    class ResumeService:
        async def resume(self, *, run_id, request):
            return AgentResumeResult(
                run_id=run_id,
                trace_id="trace-resume",
                terminal_state="running",
                resumed=True,
            )

    def capture_spawn(coroutine, *, name: str) -> None:
        spawned.append((name, coroutine))
        coroutine.close()

    monkeypatch.setattr(agent_routes, "_service", lambda _: ResumeService())
    monkeypatch.setattr(agent_routes.agent_task_registry, "spawn", capture_spawn)

    result = await agent_routes.resume_agent_run(
        run_id,
        request,
        session,  # type: ignore[arg-type]
        session_factory=object(),  # type: ignore[arg-type]
        trace_id="trace-resume",
    )

    assert result.resumed is True
    assert result.terminal_state == "running"
    assert [name for name, _ in spawned] == [f"agent-resume-{run_id}"]
    assert session.rolled_back is False


@pytest.mark.asyncio
async def test_resume_route_does_not_spawn_for_idempotent_or_stopped_result(monkeypatch) -> None:
    request = _resume_request()
    run_id = uuid4()

    class ReplayService:
        async def resume(self, *, run_id, request):
            return AgentResumeResult(
                run_id=run_id,
                trace_id="trace-replay",
                terminal_state="approval_required",
                resumed=False,
                idempotent_replay=True,
            )

    def fail_spawn(*_, **__) -> None:
        pytest.fail("an idempotent or stopped resume must not spawn execution")

    monkeypatch.setattr(agent_routes, "_service", lambda _: ReplayService())
    monkeypatch.setattr(agent_routes.agent_task_registry, "spawn", fail_spawn)

    result = await agent_routes.resume_agent_run(
        run_id,
        request,
        FakeSession(),  # type: ignore[arg-type]
        session_factory=object(),  # type: ignore[arg-type]
        trace_id="trace-replay",
    )

    assert result.idempotent_replay is True
    assert result.resumed is False


@pytest.mark.asyncio
async def test_resume_route_preserves_typed_safe_error(monkeypatch) -> None:
    request = _resume_request()
    session = FakeSession()

    class StaleResumeService:
        async def resume(self, **_):
            raise AgentResumeError(
                "stale_interrupt_cursor",
                "The requested interrupt is no longer current.",
            )

    monkeypatch.setattr(agent_routes, "_service", lambda _: StaleResumeService())

    with pytest.raises(HTTPException) as caught:
        await agent_routes.resume_agent_run(
            uuid4(),
            request,
            session,  # type: ignore[arg-type]
            session_factory=object(),  # type: ignore[arg-type]
            trace_id="trace-stale",
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == {
        "code": "stale_interrupt_cursor",
        "message": "The requested interrupt is no longer current.",
        "trace_id": "trace-stale",
        "retryable": False,
        "field_details": [],
    }
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_sustainability_route_forwards_bounded_filters(monkeypatch) -> None:
    company_id = uuid4()
    from_time = datetime(2026, 10, 1, tzinfo=UTC)
    to_time = datetime(2026, 10, 2, tzinfo=UTC)
    captured: dict[str, object] = {}
    metrics = AgentSustainabilityMetrics(
        company_id=company_id,
        from_time=from_time,
        to_time=to_time,
        run_count=1,
        completed_run_count=1,
        interrupted_run_count=0,
        provider_call_count=1,
        tool_call_count=2,
        input_tokens=10,
        output_tokens=5,
        cached_input_tokens=0,
        retry_count=0,
        cache_hits=0,
        latency_ms=25,
        estimated_energy_wh=Decimal("0.001"),
        estimated_co2e_g=Decimal("0.0004"),
        proxy_coverage_runs=1,
        assumptions=SustainabilityAssumptions(
            method="token_energy_proxy",
            version="1.0",
            energy_wh_per_1k_tokens=Decimal("0.1"),
            grid_intensity_gco2e_per_kwh=Decimal(400),
            caveat="This is a documented proxy, not a direct carbon measurement.",
        ),
    )

    class MetricsService:
        async def get_metrics(self, **kwargs):
            captured.update(kwargs)
            return metrics

    monkeypatch.setattr(
        agent_routes,
        "_sustainability_service",
        lambda _: MetricsService(),
    )

    result = await agent_routes.get_agent_sustainability_metrics(
        FakeSession(),  # type: ignore[arg-type]
        company_id,
        from_time=from_time,
        to_time=to_time,
        workflow="four_module",
        provider="gemini",
        trace_id="trace-metrics",
    )

    assert result is metrics
    assert captured == {
        "company_id": company_id,
        "from_time": from_time,
        "to_time": to_time,
        "workflow": "four_module",
        "provider": "gemini",
    }
