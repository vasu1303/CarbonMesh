from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models.carbon import CarbonMeasurement
from app.db.models.core import Company, DataSource
from app.modules.demo import service
from app.modules.demo.fixtures import (
    CURRENT_PRODUCT_ID,
    DEMO_ANALYST_ID,
    DEMO_APPROVER_ID,
    DEMO_COMPANY_ID,
    DEMO_PROCUREMENT_MANAGER_ID,
    DISPATCH_METHOD_ID,
    DISPATCH_POLICY_ID,
    FLEXIBLE_LOAD_ID,
    SCORING_METHOD_ID,
    build_demo_records,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [Company, DataSource])
async def test_reset_preserves_non_synthetic_data(e2e_context, model):
    async with e2e_context.session_factory() as session, session.begin():
        await session.execute(update(model).values(is_synthetic=False))
    async with e2e_context.session_factory() as session:
        with pytest.raises(service.DemoResetBlockedError):
            await service.reset_and_seed_demo(session)
    async with e2e_context.session_factory() as session:
        assert await session.get(CarbonMeasurement, e2e_context.ids.measurement_id) is not None
        assert await session.scalar(select(func.count()).select_from(model)) > 0


@pytest.mark.asyncio
async def test_reset_rolls_back_truncate_and_partial_seed(e2e_context, monkeypatch):
    records = build_demo_records()
    company = next(record for record in records if isinstance(record, Company))
    duplicate = Company(id=uuid4(), code=company.code, name="Synthetic invalid seed",
                        is_synthetic=True)
    monkeypatch.setattr(service, "build_demo_records", lambda: (*records, duplicate))
    async with e2e_context.session_factory() as session:
        with pytest.raises(IntegrityError):
            await service.reset_and_seed_demo(session)
    async with e2e_context.session_factory() as session:
        assert await session.get(CarbonMeasurement, e2e_context.ids.measurement_id) is not None
        assert await session.get(Company, duplicate.id) is None


@pytest.mark.asyncio
async def test_reset_replays_identical_synthetic_seed(e2e_context):
    summaries = []
    for _ in range(2):
        async with e2e_context.session_factory() as session:
            summaries.append(await service.reset_and_seed_demo(session))
    assert summaries[0] == summaries[1]
    assert summaries[0].company_id == DEMO_COMPANY_ID
    assert summaries[0].supplier_product_count == 4
    async with e2e_context.session_factory() as session:
        company = await session.get(Company, DEMO_COMPANY_ID)
        assert company.is_synthetic
        assert "Maverick" in company.name
        assert await session.scalar(select(func.count()).select_from(CarbonMeasurement)) == 0


@pytest.mark.asyncio
async def test_reset_measurement_procurement_dispatch_and_generic_decisions(api_client, e2e_context):
    async with e2e_context.session_factory() as session:
        seeded = await service.reset_and_seed_demo(session)
    company = {"company_id": str(seeded.company_id)}
    site = {**company, "site_id": str(seeded.site_id)}
    context = {**site, "reporting_period_id": str(seeded.reporting_period_id)}
    measured = await api_client.post(
        "/api/measurement/calculate",
        json={**context, "material_code": "RECYCLED-ALUMINIUM", "actor_id": str(DEMO_ANALYST_ID)},
    )
    assert measured.status_code == 200, measured.text
    measurement = measured.json()
    assert measurement["value_kgco2e"] == "86000.000000"
    procurement = await api_client.post(
        "/api/procurement/scenarios",
        json={
            **context, "current_product_id": str(CURRENT_PRODUCT_ID),
            "carbon_measurement_id": measurement["id"],
            "method_definition_id": str(SCORING_METHOD_ID),
            "requested_by": str(DEMO_PROCUREMENT_MANAGER_ID),
            "quantity": "10000", "quantity_unit": "kg", "max_cost_increase_pct": "5",
            "max_lead_time_days": 20, "minimum_circularity_score": "50",
        },
    )
    assert procurement.status_code == 201, procurement.text
    forecasts = await api_client.post(
        "/api/dispatch/forecasts/sync", json={**site, "source_mode": "fixture"}
    )
    assert forecasts.status_code == 200, forecasts.text
    scenario = await api_client.post(
        "/api/dispatch/scenarios",
        json={
            **site, "flexible_load_id": str(FLEXIBLE_LOAD_ID),
            "method_definition_id": str(DISPATCH_METHOD_ID),
            "policy_definition_id": str(DISPATCH_POLICY_ID),
            "forecast_source_document_id": forecasts.json()["source_document_id"],
            "requested_by": str(DEMO_ANALYST_ID), "window_start": "2026-10-01T08:00:00Z",
            "window_end": "2026-10-01T20:00:00Z", "baseline_start": "2026-10-01T08:00:00Z",
            "maximum_delay_minutes": 240,
        },
    )
    assert scenario.status_code == 201, scenario.text
    optimized = await api_client.post(
        f"/api/dispatch/scenarios/{scenario.json()['id']}/optimize", json=company
    )
    assert optimized.status_code == 200, optimized.text
    approvals = [
        procurement.json()["selected_recommendation"]["approval"],
        optimized.json()["recommendation"]["approval"],
    ]
    for approval in approvals:
        decision = await api_client.post(
            f"/api/approvals/{approval['id']}/decision",
            json={**company, "actor_id": str(DEMO_APPROVER_ID), "decision": "approve",
                  "preview_hash": approval["preview_hash"]},
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["status"] == "approved"
    assert optimized.json()["recommendation"]["actuation_authorized"] is False
