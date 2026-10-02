from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.models.carbon import EmissionFactor, GridIntensityPoint
from app.db.models.core import DataSource, Site
from app.main import app
from app.modules.integrations import repository as integration_repository
from app.modules.integrations.electricity_maps import get_electricity_maps_client
from tests.e2e.conftest import E2EContext


@pytest.mark.asyncio
async def test_measurement_lineage_and_audit_are_tenant_scoped(
    api_client,
    e2e_context: E2EContext,
) -> None:
    ids = e2e_context.ids
    company_query = {"company_id": str(ids.company_id)}

    lineage_response = await api_client.get(
        f"/api/measurements/{ids.measurement_id}/lineage",
        params=company_query,
    )
    assert lineage_response.status_code == 200, lineage_response.text
    lineage = lineage_response.json()
    assert lineage["root_event_id"] == str(ids.measurement_ledger_event_id)
    assert {node["event_type"] for node in lineage["nodes"] if node["event_type"]} >= {
        "activity.normalized",
        "factor.selected",
        "emissions.calculated",
        "measurement.verified",
    }
    assert any(node["node_type"] == "evidence" for node in lineage["nodes"])
    assert len(lineage["edges"]) >= 4

    audit_response = await api_client.get(
        f"/api/audit/measurement/{ids.measurement_id}",
        params=company_query,
    )
    assert audit_response.status_code == 200, audit_response.text
    audit = audit_response.json()
    assert any(item["action"] == "measurement.verified" for item in audit["timeline"])
    assert audit["lineage"]["events"]

    wrong_company = {"company_id": str(uuid4())}
    wrong_lineage_response = await api_client.get(
        f"/api/measurements/{ids.measurement_id}/lineage",
        params=wrong_company,
    )
    assert wrong_lineage_response.status_code == 404
    assert wrong_lineage_response.json()["detail"]["trace_id"]
    assert (
        await api_client.get(
            f"/api/audit/measurement/{ids.measurement_id}",
            params=wrong_company,
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_approval_decision_validates_hash_and_is_idempotent(
    api_client,
    e2e_context: E2EContext,
) -> None:
    ids = e2e_context.ids
    company_query = {"company_id": str(ids.company_id)}
    scenario_response = await api_client.post(
        "/api/procurement/scenarios",
        json={
            "company_id": str(ids.company_id),
            "site_id": str(ids.site_id),
            "reporting_period_id": str(ids.reporting_period_id),
            "current_product_id": str(ids.current_product_id),
            "carbon_measurement_id": str(ids.measurement_id),
            "method_definition_id": str(ids.scoring_method_id),
            "requested_by": str(ids.procurement_manager_id),
            "quantity": "10000",
            "quantity_unit": "kg",
            "max_cost_increase_pct": "5",
            "max_lead_time_days": 20,
            "minimum_circularity_score": "50",
        },
    )
    assert scenario_response.status_code == 201, scenario_response.text
    recommendation = scenario_response.json()["selected_recommendation"]
    approval_id = recommendation["approval"]["id"]
    preview_hash = recommendation["approval"]["preview_hash"]

    pending_response = await api_client.get("/api/approvals", params=company_query)
    assert pending_response.status_code == 200
    assert pending_response.json()["items"][0]["status"] == "pending"
    assert pending_response.json()["items"][0]["preview_current"] is True

    invalid_response = await api_client.post(
        f"/api/approvals/{approval_id}/decision",
        json={
            "company_id": str(ids.company_id),
            "decision": "approve",
            "preview_hash": "0" * 64,
            "actor_id": str(ids.approver_id),
        },
    )
    assert invalid_response.status_code == 409
    assert invalid_response.json()["detail"]["code"] == "approval_invalidated"
    assert invalid_response.json()["detail"]["trace_id"]

    cross_tenant_response = await api_client.post(
        f"/api/approvals/{approval_id}/decision",
        json={
            "company_id": str(uuid4()),
            "decision": "approve",
            "preview_hash": preview_hash,
            "actor_id": str(ids.approver_id),
        },
    )
    assert cross_tenant_response.status_code == 404
    assert cross_tenant_response.json()["detail"]["code"] == "approval_not_found"

    missing_response = await api_client.post(
        f"/api/approvals/{uuid4()}/decision",
        json={
            "company_id": str(ids.company_id),
            "decision": "approve",
            "preview_hash": preview_hash,
            "actor_id": str(ids.approver_id),
        },
    )
    assert missing_response.status_code == cross_tenant_response.status_code
    for field in ("code", "message", "retryable", "field_details"):
        assert (
            missing_response.json()["detail"][field]
            == cross_tenant_response.json()["detail"][field]
        )

    decision_payload = {
        "company_id": str(ids.company_id),
        "decision": "approve",
        "preview_hash": preview_hash,
        "actor_id": str(ids.approver_id),
        "decision_note": "Synthetic POC approval.",
    }
    decision_response = await api_client.post(
        f"/api/approvals/{approval_id}/decision",
        json=decision_payload,
        headers={"X-Trace-ID": "synthetic-approval-trace"},
    )
    assert decision_response.status_code == 200, decision_response.text
    decision = decision_response.json()
    assert decision["status"] == "approved"
    assert decision["preview_hash"] == preview_hash
    assert decision["idempotent_replay"] is False

    replay_response = await api_client.post(
        f"/api/approvals/{approval_id}/decision",
        json=decision_payload,
    )
    assert replay_response.status_code == 200
    assert replay_response.json()["idempotent_replay"] is True
    assert replay_response.json()["ledger_event_id"] == decision["ledger_event_id"]

    approved_response = await api_client.get(
        "/api/approvals",
        params={**company_query, "status": "approved"},
    )
    assert approved_response.status_code == 200
    assert approved_response.json()["total"] == 1

    audit_response = await api_client.get(
        f"/api/audit/approval/{approval_id}",
        params=company_query,
    )
    assert audit_response.status_code == 200, audit_response.text
    audit = audit_response.json()
    assert any(item["action"] == "approval.approved" for item in audit["timeline"])
    assert any(edge["relationship_type"] == "decided_by" for edge in audit["lineage"]["edges"])


@pytest.mark.asyncio
async def test_electricity_maps_history_points_are_immutable_and_site_scoped(
    api_client,
    e2e_context: E2EContext,
) -> None:
    ids = e2e_context.ids
    second_site_id = uuid4()
    async with e2e_context.session_factory() as session, session.begin():
        session.add(
            Site(
                id=second_site_id,
                company_id=ids.company_id,
                code="PLANT-C",
                name="Plant C",
                country_code="IN",
                timezone="Asia/Kolkata",
            )
        )

    fake_provider = FakeElectricityMapsProvider()
    app.dependency_overrides[get_electricity_maps_client] = lambda: fake_provider
    try:
        token_test = await api_client.post(
            "/api/integrations/electricity-maps/test",
            params={"max_zones": 10},
        )
        assert token_test.status_code == 200, token_test.text
        assert token_test.json()["authenticated"] is True
        assert token_test.json()["zones"][0]["zone"] == "IN"
        assert "token" not in token_test.text.lower()

        company_query = {"company_id": str(ids.company_id)}
        sync_payload = {
            "mode": "live",
            "start": "2026-09-29T00:00:00Z",
            "end": "2026-09-29T03:00:00Z",
        }
        first_response, concurrent_response = await asyncio.gather(
            api_client.post(
                "/api/measurement/grid/history/sync",
                params={**company_query, "site_id": str(ids.site_id)},
                json=sync_payload,
            ),
            api_client.post(
                "/api/measurement/grid/history/sync",
                params={**company_query, "site_id": str(ids.site_id)},
                json=sync_payload,
            ),
        )
        responses = [first_response, concurrent_response]
        assert all(response.status_code == 200 for response in responses), [
            response.text for response in responses
        ]
        assert sorted(response.json()["inserted_points"] for response in responses) == [0, 2]
        assert sorted(response.json()["existing_points"] for response in responses) == [0, 2]
        sync_response = next(
            response for response in responses if response.json()["inserted_points"] == 2
        )
        assert sync_response.status_code == 200, sync_response.text
        sync = sync_response.json()
        assert sync["zone"] == "IN"
        assert sync["zone_resolution"] == "configured"
        assert sync["received_points"] == 2
        assert sync["inserted_points"] == 2
        assert sync["estimated_points"] == 1

        repeated_response = await api_client.post(
            "/api/measurement/grid/history/sync",
            params={**company_query, "site_id": str(ids.site_id)},
            json=sync_payload,
        )
        assert repeated_response.status_code == 200, repeated_response.text
        assert repeated_response.json()["inserted_points"] == 0
        assert repeated_response.json()["existing_points"] == 2

        second_site_sync = await api_client.post(
            "/api/measurement/grid/history/sync",
            params={**company_query, "site_id": str(second_site_id)},
            json=sync_payload,
        )
        assert second_site_sync.status_code == 200, second_site_sync.text
        assert second_site_sync.json()["zone"] == "IN"
        assert second_site_sync.json()["zone_resolution"] == "country_exact"
        assert second_site_sync.json()["inserted_points"] == 2
        assert second_site_sync.json()["existing_points"] == 0

        async with e2e_context.session_factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(GridIntensityPoint)
                    .where(
                        GridIntensityPoint.observed_at
                        >= datetime(2026, 9, 29, tzinfo=UTC),
                        GridIntensityPoint.observed_at
                        < datetime(2026, 9, 29, 3, tzinfo=UTC),
                    )
                )
                == 4
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(DataSource)
                    .where(
                        DataSource.company_id == ids.company_id,
                        DataSource.site_id == ids.site_id,
                        DataSource.external_reference
                        == integration_repository.ELECTRICITY_MAPS_EXTERNAL_REFERENCE,
                    )
                )
                == 1
            )
            # Historical provider points must not be smuggled back into the
            # generic purchased-material factor catalogue.
            assert await session.scalar(select(func.count()).select_from(EmissionFactor)) == 1

        latest_response = await api_client.get(
            "/api/measurement/grid/latest",
            params={**company_query, "site_id": str(ids.site_id)},
        )
        assert latest_response.status_code == 200, latest_response.text
        latest = latest_response.json()
        assert latest["zone"] == "IN"
        assert latest["value"] == "0.480000000000"
        assert Decimal(latest["provider_value_gco2eq_per_kwh"]) == Decimal(480)
        assert latest["is_estimated"] is True
        assert latest["provenance"]["response_checksum"] == sync["response_checksum"]
        assert latest["provenance"]["cache_scope"] == "site_zone"

        second_site_latest_response = await api_client.get(
            "/api/measurement/grid/latest",
            params={**company_query, "site_id": str(second_site_id)},
        )
        assert second_site_latest_response.status_code == 200, second_site_latest_response.text
        second_site_latest = second_site_latest_response.json()
        assert second_site_latest["grid_intensity_point_id"] != latest[
            "grid_intensity_point_id"
        ]
        assert second_site_latest["value"] == latest["value"]
        assert second_site_latest["zone"] == "IN"
        assert second_site_latest["provenance"]["source_site_id"] == str(second_site_id)

        wrong_tenant_response = await api_client.get(
            "/api/measurement/grid/latest",
            params={"company_id": str(uuid4()), "site_id": str(ids.site_id)},
        )
        assert wrong_tenant_response.status_code == 404
        assert wrong_tenant_response.json()["detail"]["trace_id"]
    finally:
        app.dependency_overrides.pop(get_electricity_maps_client, None)


class FakeElectricityMapsProvider:
    async def list_zones(self):
        return {
            "IN": {
                "zoneKey": "IN",
                "zoneName": "India",
                "countryCode": "IN",
                "access": ["carbon-intensity/past-range"],
            }
        }

    async def get_carbon_intensity_range(
        self,
        *,
        zone: str,
        start: datetime,
        end: datetime,
        disable_estimations: bool = False,
    ):
        assert zone == "IN"
        assert start == datetime(2026, 9, 29, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 9, 29, 3, 0, tzinfo=UTC)
        assert disable_estimations is False
        return {
            "zone": "IN",
            "temporalGranularity": "hourly",
            "aggregationPeriod": "hourly",
            "data": [
                {
                    "zone": "IN",
                    "carbonIntensity": 475,
                    "datetime": "2026-09-29T00:00:00Z",
                    "updatedAt": "2026-09-29T00:15:00Z",
                    "createdAt": "2026-09-29T00:10:00Z",
                    "emissionFactorType": "lifecycle",
                    "flowTraced": True,
                    "isEstimated": False,
                    "estimationMethod": None,
                    "temporalGranularity": "hourly",
                },
                {
                    "zone": "IN",
                    "carbonIntensity": 480,
                    "datetime": "2026-09-29T01:00:00Z",
                    "updatedAt": "2026-09-29T01:15:00Z",
                    "createdAt": "2026-09-29T01:10:00Z",
                    "emissionFactorType": "lifecycle",
                    "flowTraced": True,
                    "isEstimated": True,
                    "estimationMethod": "FORECASTS_HIERARCHY",
                    "temporalGranularity": "hourly",
                },
            ],
        }
