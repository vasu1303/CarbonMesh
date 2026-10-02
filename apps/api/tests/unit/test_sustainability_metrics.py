from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.modules.agents.schemas import SustainabilityBenefitFact
from app.modules.agents.sustainability import AgentSustainabilityService


def measured_run(*, assumptions=True, energy="0.6", co2="0.285"):
    return SimpleNamespace(
        id=uuid4(), terminal_state="success", model_calls=1, tool_calls=8,
        input_tokens=1000, output_tokens=1000, retry_count=1, api_calls=3,
        latency_ms=3000, estimated_energy_wh=Decimal(energy), estimated_co2e_g=Decimal(co2),
        telemetry={"cache_hits": 2, "sustainability_assumptions": {
            "method": "token_energy_proxy", "version": "1.0",
            "energy_wh_per_1k_tokens": "0.3", "grid_intensity_gco2e_per_kwh": "475",
        } if assumptions else None},
    )


class MetricsRepository:
    def __init__(self, runs, facts):
        self.runs = runs
        self.facts = facts

    async def list_runs(self, **kwargs):
        return self.runs

    async def list_provider_steps(self, **kwargs):
        return []

    async def list_benefit_facts(self, **kwargs):
        return self.facts


@pytest.mark.asyncio
async def test_projected_benefit_uses_ledger_facts_and_persisted_footprint_assumptions():
    fact = SustainabilityBenefitFact(
        target_type="dispatch_recommendation", target_id=uuid4(), ledger_event_id=uuid4(),
        avoided_kgco2e=Decimal("0.285"),
    )
    service = AgentSustainabilityService(
        MetricsRepository([measured_run()], [fact]),
        settings=Settings(ai_energy_wh_per_1k_tokens=Decimal(999)),
    )
    result = await service.get_metrics(company_id=uuid4())
    assert result.business_benefit_kgco2e == Decimal("0.285")
    assert result.benefit_to_footprint_ratio == Decimal(1000)
    assert result.benefit_facts == [fact]
    assert result.assumptions.energy_wh_per_1k_tokens == Decimal("0.3")
    assert result.external_api_calls == 3
    assert result.cache_hits == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unknown", "zero", "mixed", "empty"])
async def test_ratio_is_absent_without_complete_comparable_nonzero_footprint(kind):
    runs = [measured_run()]
    if kind == "unknown":
        runs.append(measured_run(assumptions=False))
    elif kind == "zero":
        runs = [measured_run(energy="0", co2="0")]
    elif kind == "mixed":
        another = measured_run()
        another.telemetry["sustainability_assumptions"]["grid_intensity_gco2e_per_kwh"] = "600"
        runs.append(another)
    else:
        runs = []
    result = await AgentSustainabilityService(MetricsRepository(runs, [])).get_metrics(
        company_id=uuid4(),
    )
    assert result.benefit_to_footprint_ratio is None
