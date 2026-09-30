from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.db.models.ledger import LedgerEventEvidence, LineageEdge
from app.db.models.procurement import (
    Approval,
    FactBinding,
    ProcurementScenario,
    Recommendation,
    SupplierProduct,
    SupplierScore,
)
from tests.e2e.conftest import E2EContext


@pytest.mark.asyncio
async def test_procurement_api_vertical_slice_is_deterministic(
    api_client, e2e_context: E2EContext
) -> None:
    ids = e2e_context.ids
    company_query = {"company_id": str(ids.company_id)}

    supplier_list = await api_client.get("/api/v1/suppliers", params=company_query)
    assert supplier_list.status_code == 200
    assert supplier_list.json()["total"] == 3
    assert {item["id"] for item in supplier_list.json()["items"]} == {
        str(ids.current_product_id),
        str(ids.recommended_product_id),
        str(ids.expensive_product_id),
    }

    product_detail = await api_client.get(
        f"/api/v1/suppliers/{ids.recommended_product_id}", params=company_query
    )
    assert product_detail.status_code == 200
    assert product_detail.json()["pcf_kgco2e_per_unit"] == "1.900000000000"
    assert product_detail.json()["risk"] == "low"
    assert product_detail.json()["evidence"]["content_text"]

    common_scenario = {
        "company_id": str(ids.company_id),
        "site_id": str(ids.site_id),
        "reporting_period_id": str(ids.reporting_period_id),
        "current_product_id": str(ids.current_product_id),
        "carbon_measurement_id": str(ids.measurement_id),
        "method_definition_id": str(ids.scoring_method_id),
        "requested_by": str(ids.procurement_manager_id),
        "quantity": "12000",
        "quantity_unit": "kg",
        "currency": "USD",
        "max_cost_increase_pct": "5",
        "max_lead_time_days": 20,
        "minimum_circularity_score": "50",
    }
    unknown_constraint = await api_client.post(
        "/api/v1/procurement/scenarios",
        json={
            **common_scenario,
            "material_constraints": {"minimum_recycled_content": "50"},
        },
    )
    assert unknown_constraint.status_code == 422

    zero_cost = await api_client.post(
        "/api/v1/procurement/scenarios",
        json={**common_scenario, "current_unit_cost": "0"},
    )
    assert zero_cost.status_code == 422
    assert zero_cost.json()["detail"]["code"] == "invalid_current_unit_cost"

    scenario_payload = {
        **common_scenario,
        "current_unit_cost": "1.00",
        "material_constraints": {"excluded_risk_levels": ["high"]},
    }
    scenario_response = await api_client.post(
        "/api/v1/procurement/scenarios",
        json=scenario_payload,
    )
    assert scenario_response.status_code == 201, scenario_response.text
    scenario = scenario_response.json()
    assert scenario["terminal_state"] == "completed"
    assert scenario["status"] == "recommended"
    assert len(scenario["alternatives"]) == 2
    recommendation = scenario["selected_recommendation"]
    assert recommendation["recommended_product_id"] == str(ids.recommended_product_id)
    assert recommendation["impact"] == {
        "projected_footprint_kgco2e": "22800.000000",
        "avoided_kgco2e": "10800.000000",
        "reduction_pct": "32.1429",
        "cost_delta_pct": "3.2000",
        "lead_time_delta_days": 2,
    }
    assert recommendation["approval"]["status"] == "pending"
    approval_id = recommendation["approval"]["id"]
    preview_hash = recommendation["approval"]["preview_hash"]
    scenario_id = scenario["id"]
    recommendation_id = recommendation["id"]

    reused_response = await api_client.post(
        "/api/v1/procurement/scenarios",
        json=scenario_payload,
    )
    assert reused_response.status_code == 201, reused_response.text
    reused = reused_response.json()
    assert reused["id"] == scenario_id
    assert reused["selected_recommendation"]["id"] == recommendation_id

    rerun = await api_client.post(
        "/api/v1/procurement/assessments/run",
        json={"company_id": str(ids.company_id), "scenario_id": scenario_id},
    )
    assert rerun.status_code == 200
    assert rerun.json()["recommendation_id"] == recommendation_id
    assert rerun.json()["selected_product_id"] == str(ids.recommended_product_id)

    scenario_detail = await api_client.get(
        f"/api/v1/procurement/scenarios/{scenario_id}", params=company_query
    )
    assert scenario_detail.status_code == 200
    assert scenario_detail.json()["analysis_signature"] == scenario["analysis_signature"]

    recommendation_detail = await api_client.get(
        f"/api/v1/procurement/recommendations/{recommendation_id}",
        params=company_query,
    )
    assert recommendation_detail.status_code == 200
    detail = recommendation_detail.json()
    assert detail["payload_hash"] == recommendation["payload_hash"]
    assert len(detail["payload_hash"]) == 64
    assert detail["narrative"]["unsupported_fragments"] == []
    assert "{" not in detail["narrative"]["resolved_text"]
    assert len(detail["evidence"]) == 2
    assert len(detail["impact_snapshot"]["review"]["fact_bindings"]) == 7
    assert all(binding["id"] is None for binding in detail["narrative"]["fact_bindings"])

    tight_response = await api_client.post(
        "/api/v1/procurement/scenarios",
        json={
            **common_scenario,
            "max_cost_increase_pct": "3",
        },
    )
    assert tight_response.status_code == 201, tight_response.text
    tight_result = tight_response.json()
    assert tight_result["terminal_state"] == "no_feasible_option"
    assert tight_result["status"] == "assessed"
    assert tight_result["selected_recommendation"] is None
    assert all(item["feasible"] is False for item in tight_result["alternatives"])
    assert any(
        reason["code"] == "cost_ceiling_exceeded"
        for item in tight_result["alternatives"]
        for reason in item["infeasibility_reasons"]
    )
    assert Decimal(tight_result["constraints"]["max_cost_increase_pct"]) == Decimal(3)

    async with e2e_context.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ProcurementScenario)) == 2
        assert await session.scalar(select(func.count()).select_from(SupplierScore)) == 4
        assert await session.scalar(select(func.count()).select_from(Recommendation)) == 1
        assert await session.scalar(select(func.count()).select_from(Approval)) == 1
        assert await session.scalar(select(func.count()).select_from(FactBinding)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LineageEdge)
                .where(LineageEdge.relationship_type == "informed_recommendation")
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEventEvidence)
                .where(LedgerEventEvidence.ledger_event_id == UUID(detail["ledger_event_id"]))
            )
            == 2
        )

    async with e2e_context.session_factory() as session, session.begin():
        product = await session.get(SupplierProduct, ids.recommended_product_id)
        assert product is not None
        product.pcf_kgco2e_per_unit = Decimal("1.8")

    frozen_scenario = await api_client.get(
        f"/api/v1/procurement/scenarios/{scenario_id}", params=company_query
    )
    assert frozen_scenario.status_code == 200
    frozen_selected = next(
        item
        for item in frozen_scenario.json()["alternatives"]
        if item["product"]["id"] == str(ids.recommended_product_id)
    )
    assert Decimal(frozen_selected["product"]["pcf_kgco2e_per_unit"]) == Decimal("1.9")
    assert Decimal(frozen_selected["impact"]["projected_footprint_kgco2e"]) == Decimal(22800)

    frozen_recommendation = await api_client.get(
        f"/api/v1/procurement/recommendations/{recommendation_id}",
        params=company_query,
    )
    assert frozen_recommendation.status_code == 200
    assert Decimal(
        frozen_recommendation.json()["recommended_product"]["pcf_kgco2e_per_unit"]
    ) == Decimal("1.9")
    assert Decimal(frozen_recommendation.json()["avoided_kgco2e"]) == Decimal(10800)

    stale_queue = await api_client.get(
        "/api/v1/approvals", params={**company_query, "status": "pending"}
    )
    assert stale_queue.status_code == 200
    assert stale_queue.json()["items"][0]["preview_current"] is False

    stale_decision = await api_client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={
            "company_id": str(ids.company_id),
            "decision": "approve",
            "preview_hash": preview_hash,
            "actor_id": str(ids.approver_id),
        },
    )
    assert stale_decision.status_code == 409
    assert stale_decision.json()["detail"]["code"] == "approval_invalidated"
