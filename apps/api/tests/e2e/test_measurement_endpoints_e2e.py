from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.db.models.carbon import CarbonMeasurement, EmissionCalculation, GridIntensityPoint
from app.db.models.ledger import LedgerEvent
from app.main import app
from app.modules.demo.fixtures import (
    ASSURANCE_STANDARD_ID,
    DEMO_ANALYST_ID,
    ELECTRICITY_ACTIVITY_METRIC_ID,
)
from app.modules.demo.service import reset_and_seed_demo
from app.modules.integrations.electricity_maps import get_electricity_maps_client
from tests.e2e.test_platform_endpoints_e2e import FakeElectricityMapsProvider


@pytest.mark.asyncio
async def test_hourly_import_grid_measurement_breakdown_lineage_and_assurance(
    api_client, e2e_context
):
    # The E2E fixture owns a dedicated temporary PostgreSQL cluster. Reset never
    # uses the configured application database.
    async with e2e_context.session_factory() as session:
        seeded = await reset_and_seed_demo(session)
    company = {"company_id": str(seeded.company_id)}
    context = {
        **company,
        "site_id": str(seeded.site_id),
        "reporting_period_id": str(seeded.reporting_period_id),
    }
    imported = await api_client.post(
        "/api/activities/import",
        json={
            **context,
            "metric_definition_id": str(ELECTRICITY_ACTIVITY_METRIC_ID),
            "source_name": "Synthetic hourly meter test",
            "filename": "synthetic-meter.json",
            "content_type": "application/json",
            "is_synthetic": True,
            "interval_start": "2026-09-29T00:00:00Z",
            "interval_end": "2026-09-29T02:00:00Z",
            "content": [
                {
                    "row_key": "synthetic-hour-0",
                    "timestamp": "2026-09-29T05:30:00+05:30",
                    "kwh": "100",
                    "unit": "kWh",
                },
                {
                    "row_key": "synthetic-hour-1",
                    "timestamp": "2026-09-29T01:00:00Z",
                    "kwh": "200",
                    "unit": "kWh",
                },
            ],
        },
    )
    assert imported.status_code == 201, imported.text
    calculate_payload = {
        **context,
        "output_metric_key": "emissions.scope2.location_based",
        "actor_id": str(DEMO_ANALYST_ID),
    }
    no_grid = await api_client.post("/api/measurement/calculate", json=calculate_payload)
    assert no_grid.status_code == 422, no_grid.text
    assert no_grid.json()["detail"]["code"] == "missing_grid_interval"

    app.dependency_overrides[get_electricity_maps_client] = FakeElectricityMapsProvider
    try:
        synchronized = await api_client.post(
            "/api/measurement/grid/history/sync",
            params={**company, "site_id": str(seeded.site_id)},
            json={"mode": "live", "start": "2026-09-29T00:00:00Z", "end": "2026-09-29T03:00:00Z"},
        )
        assert synchronized.status_code == 200, synchronized.text
    finally:
        app.dependency_overrides.pop(get_electricity_maps_client, None)

    response = await api_client.post("/api/measurement/calculate", json=calculate_payload)
    assert response.status_code == 200, response.text
    measured = response.json()
    assert Decimal(measured["value_kgco2e"]) == Decimal("143.500000")
    assert measured["calculation_run"]["method_version"] == "2.0.0"
    assert len(measured["calculation_run"]["method_hash"]) == 64
    assert len(measured["calculation_run"]["code_hash"]) == 64
    assert measured["confidence_breakdown"]["version"] == "2.0.0"
    assert Decimal(measured["confidence"]) == Decimal("0.72435")
    assert measured["coverage"]["observed_hours"] == 2
    assert measured["coverage"]["reporting_period_hours"] == 2208
    assert measured["coverage"]["full_reporting_period"] is False
    assert measured["factors"] == []
    assert len(measured["grid_points"]) == len(measured["calculations"]) == 2
    assert measured["calculations"][0]["normalized_quantity_kg"] is None
    assert all(row["grid_intensity_point_id"] for row in measured["calculations"])
    measurement_id = measured["id"]
    replay = await api_client.post("/api/measurement/calculate", json=calculate_payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == measurement_id
    assert replay.json()["idempotent"] is True
    assert replay.json()["output_hash"] == measured["output_hash"]

    breakdown = await api_client.get(
        f"/api/measurements/{measurement_id}/breakdown", params=company
    )
    assert breakdown.status_code == 200, breakdown.text
    assert sum(Decimal(row["emissions_kgco2e"]) for row in breakdown.json()["items"]) == Decimal(
        "143.5"
    )
    assert breakdown.json()["items"][0]["quantity_unit"] == "kWh"
    assert (
        await api_client.get(
            f"/api/measurements/{measurement_id}/breakdown", params={"company_id": str(uuid4())}
        )
    ).status_code == 404
    lineage = await api_client.get(f"/api/measurements/{measurement_id}/lineage", params=company)
    assert lineage.status_code == 200, lineage.text
    assert any(
        node.get("event_type") == "measurement.grid_point_used" for node in lineage.json()["nodes"]
    )
    assert any(node["node_type"] == "evidence" for node in lineage.json()["nodes"])

    draft = await api_client.post(
        "/api/assurance/drafts",
        json={
            **context,
            "standard_id": str(ASSURANCE_STANDARD_ID),
            "measurement_id": measurement_id,
            "requested_by": str(DEMO_ANALYST_ID),
            "idempotency_key": "synthetic-hourly-assurance",
            "title": "Synthetic hourly Scope 2 disclosure",
        },
    )
    assert draft.status_code == 201, draft.text
    validation = await api_client.post(
        f"/api/assurance/drafts/{draft.json()['id']}/validate",
        json={
            **company,
            "requested_by": str(DEMO_ANALYST_ID),
            "idempotency_key": "hourly-assurance-validate",
        },
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["terminal_state"] == "unsupported"
    assert validation.json()["draft"]["approval"] is None

    async with e2e_context.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(EmissionCalculation)
                .where(EmissionCalculation.grid_intensity_point_id.is_not(None))
            )
            == 2
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEvent)
                .where(LedgerEvent.event_type == "measurement.verified")
            )
            == 1
        )
        point = await session.get(GridIntensityPoint, UUID(measured["grid_points"][0]["id"]))
        point.intensity_gco2e_per_kwh += Decimal(1)
        await session.commit()
    tampered = await api_client.post("/api/measurement/calculate", json=calculate_payload)
    assert tampered.status_code == 422, tampered.text
    assert tampered.json()["detail"]["code"] == "grid_evidence_mismatch"

    material = await api_client.post(
        "/api/measurement/calculate",
        json={
            **context,
            "material_code": "RECYCLED-ALUMINIUM",
            "actor_id": str(DEMO_ANALYST_ID),
        },
    )
    assert material.status_code == 200, material.text
    assert Decimal(material.json()["value_kgco2e"]) == Decimal(86000)
    assert material.json()["confidence_breakdown"]["version"] == "2.0.0"
    assert Decimal(material.json()["confidence"]) == Decimal("0.98250")


@pytest.mark.asyncio
async def test_scope2_blocks_missing_edge_hour_even_after_quality_review(api_client, e2e_context):
    async with e2e_context.session_factory() as session:
        seeded = await reset_and_seed_demo(session)
    company = {"company_id": str(seeded.company_id)}
    context = {
        **company,
        "site_id": str(seeded.site_id),
        "reporting_period_id": str(seeded.reporting_period_id),
    }
    imported = await api_client.post(
        "/api/activities/import",
        json={
            **context,
            "metric_definition_id": str(ELECTRICITY_ACTIVITY_METRIC_ID),
            "source_name": "Synthetic missing edge-hour test",
            "filename": "synthetic-gap.json",
            "content_type": "application/json",
            "is_synthetic": True,
            "interval_start": "2026-09-29T00:00:00Z",
            "interval_end": "2026-09-29T02:00:00Z",
            "content": [
                {
                    "row_key": "synthetic-hour-0",
                    "timestamp": "2026-09-29T00:00:00Z",
                    "kwh": "100",
                    "unit": "kWh",
                }
            ],
        },
    )
    assert imported.status_code == 201, imported.text
    payload = {**context, "output_metric_key": "emissions.scope2.location_based"}
    response = await api_client.post("/api/measurement/calculate", json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "activity_quality_blocked"
    issues = await api_client.get("/api/quality/issues", params=company)
    issue = next(item for item in issues.json()["items"] if item["code"] == "missing_interval")
    reviewed = await api_client.patch(
        f"/api/quality/issues/{issue['id']}",
        json={
            **company,
            "actor_id": str(DEMO_ANALYST_ID),
            "status": "resolved",
            "decision_note": "Synthetic missing data acknowledged; no readings inferred.",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    still_blocked = await api_client.post("/api/measurement/calculate", json=payload)
    assert still_blocked.status_code == 422
    assert still_blocked.json()["detail"]["code"] == "activity_quality_blocked"
    async with e2e_context.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(CarbonMeasurement)) == 0
