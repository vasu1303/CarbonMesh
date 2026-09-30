from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.procurement.review import (
    canonicalize_scenario_context,
    scenario_context_signature,
)
from app.modules.procurement.schemas import CreateScenarioRequest, ScoringWeights
from app.modules.procurement.service import ProcurementService


class TrackingSession:
    def __init__(self) -> None:
        self.rollback_count = 0

    async def rollback(self) -> None:
        self.rollback_count += 1


class ExistingScenarioRepository:
    def __init__(self, existing: object) -> None:
        self.existing = existing
        self.signature_lookups = 0

    async def get_scenario_dependencies(self, **_):
        return SimpleNamespace(
            current_product=SimpleNamespace(
                product=SimpleNamespace(unit_cost=Decimal("1.00"), currency="USD")
            ),
            method=object(),
        )

    async def find_scenario_by_signature(self, **_):
        self.signature_lookups += 1
        return self.existing


def test_scenario_signature_ignores_run_lineage_and_decimal_scale() -> None:
    first_run_id = uuid4()
    second_run_id = uuid4()
    manual_context = {
        "agent_run_id": first_run_id,
        "quantity": Decimal(12000),
        "current_product": {"unit_cost": Decimal("1.00")},
        "constraints": {
            "max_cost_increase_pct": Decimal(5),
            "minimum_circularity_score": Decimal("50.0"),
        },
    }
    agent_context = {
        "agent_run_id": second_run_id,
        "quantity": Decimal("12000.000000"),
        "current_product": {"unit_cost": Decimal("1.000000")},
        "constraints": {
            "max_cost_increase_pct": Decimal("5.0000"),
            "minimum_circularity_score": Decimal("50.0000"),
        },
    }

    normalized = canonicalize_scenario_context(manual_context)

    assert normalized["agent_run_id"] == str(first_run_id)
    assert normalized["quantity"] == "12000.000000"
    assert normalized["current_product"]["unit_cost"] == "1.000000"
    assert normalized["constraints"]["max_cost_increase_pct"] == "5.0000"
    assert scenario_context_signature(manual_context) == scenario_context_signature(agent_context)


@pytest.mark.asyncio
async def test_existing_scenario_reuse_does_not_rollback_the_callers_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = TrackingSession()
    service = ProcurementService(session)  # type: ignore[arg-type]
    existing = object()
    repository = ExistingScenarioRepository(existing)
    expected_result = object()

    monkeypatch.setattr(service, "repository", repository)
    monkeypatch.setattr(service, "_validate_dependencies", lambda *_: None)
    monkeypatch.setattr(
        service,
        "_resolve_weights",
        lambda _: ScoringWeights(
            carbon="0.4",
            evidence="0.25",
            circularity="0.2",
            operational_fit="0.15",
        ),
    )
    monkeypatch.setattr(service, "_frozen_context", lambda **_: {"scope": "stable"})

    async def scenario_result(value: object):
        assert value is existing
        return expected_result

    monkeypatch.setattr(service, "_scenario_result", scenario_result)
    request = CreateScenarioRequest(
        company_id=uuid4(),
        site_id=uuid4(),
        reporting_period_id=uuid4(),
        current_product_id=uuid4(),
        carbon_measurement_id=uuid4(),
        method_definition_id=uuid4(),
        requested_by=uuid4(),
        agent_run_id=uuid4(),
        quantity="12000",
        quantity_unit="kg",
        current_unit_cost="1.00",
        currency="USD",
    )

    result = await service.create_scenario(request)

    assert result is expected_result
    assert repository.signature_lookups == 1
    assert session.rollback_count == 0
