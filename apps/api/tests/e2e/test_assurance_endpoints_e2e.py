from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.db.models.assurance import DisclosureClaim, DisclosureDraft, EvidenceGap
from app.db.models.core import Approval
from app.db.models.ledger import FactBinding
from tests.e2e.conftest import E2EContext


@pytest.mark.asyncio
async def test_assurance_http_journey_blocks_unsupported_prior_period_claim(
    api_client,
    e2e_context: E2EContext,
) -> None:
    ids = e2e_context.ids
    company_query = {"company_id": str(ids.company_id)}
    other_company_id = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")

    standards_response = await api_client.get(
        "/api/assurance/standards",
        params=company_query,
    )
    assert standards_response.status_code == 200, standards_response.text
    standards = standards_response.json()
    assert standards["total"] == 1
    assert standards["items"][0]["id"] == str(ids.assurance_standard_id)
    assert standards["items"][0]["code"] == "GHG-PROTOCOL-SCOPE-2-DEMO"
    assert [item["requirement_code"] for item in standards["items"][0]["requirements"]] == [
        "S2-BOUNDARY",
        "S2-TOTAL",
        "S2-PRIOR-PERIOD",
    ]

    other_tenant_standards = await api_client.get(
        "/api/assurance/standards",
        params={"company_id": str(other_company_id)},
    )
    assert other_tenant_standards.status_code == 200
    assert other_tenant_standards.json()["items"] == []
    assert other_tenant_standards.json()["total"] == 0

    create_payload = {
        "company_id": str(ids.company_id),
        "standard_id": str(ids.assurance_standard_id),
        "site_id": str(ids.site_id),
        "reporting_period_id": str(ids.reporting_period_id),
        "measurement_id": str(ids.measurement_id),
        "agent_run_id": str(ids.assurance_agent_run_id),
        "requested_by": str(ids.analyst_id),
        "idempotency_key": "assurance-e2e-create-q3-2026",
        "title": "Q3 2026 synthetic emissions disclosure",
    }
    create_response = await api_client.post(
        "/api/assurance/drafts",
        json=create_payload,
    )
    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    draft_id = created["id"]
    assert created["status"] == "draft"
    assert created["version"] == 1
    assert created["measurement_id"] == str(ids.measurement_id)
    assert created["agent_run_id"] == str(ids.assurance_agent_run_id)
    assert created["claims"] == []
    assert created["gaps"] == []
    assert created["approval"] is None

    replay_response = await api_client.post(
        "/api/assurance/drafts",
        json=create_payload,
    )
    assert replay_response.status_code == 201, replay_response.text
    replayed_draft = replay_response.json()
    assert replayed_draft["id"] == draft_id
    assert replayed_draft["context_hash"] == created["context_hash"]
    assert replayed_draft["payload_hash"] == created["payload_hash"]

    read_response = await api_client.get(
        f"/api/assurance/drafts/{draft_id}",
        params=company_query,
    )
    assert read_response.status_code == 200, read_response.text
    assert read_response.json()["id"] == draft_id

    wrong_tenant_read = await api_client.get(
        f"/api/assurance/drafts/{draft_id}",
        params={"company_id": str(other_company_id)},
    )
    assert wrong_tenant_read.status_code == 404
    assert wrong_tenant_read.json()["detail"]["code"] == "assurance_not_found"

    validation_payload = {
        "company_id": str(ids.company_id),
        "requested_by": str(ids.analyst_id),
        "idempotency_key": "assurance-e2e-validate-q3-2026",
    }
    validation_response = await api_client.post(
        f"/api/assurance/drafts/{draft_id}/validate",
        json=validation_payload,
    )
    assert validation_response.status_code == 200, validation_response.text
    validation = validation_response.json()
    assert validation["terminal_state"] == "unsupported"
    assert validation["supported_claims"] == 2
    assert validation["partially_supported_claims"] == 0
    assert validation["unsupported_claims"] == 1
    assert validation["open_gaps"] == 1
    assert validation["idempotent"] is False

    validated_draft = validation["draft"]
    assert validated_draft["status"] == "blocked"
    assert validated_draft["approval"] is None
    claims_by_code = {item["requirement_code"]: item for item in validated_draft["claims"]}
    assert list(claims_by_code) == ["S2-BOUNDARY", "S2-TOTAL", "S2-PRIOR-PERIOD"]
    assert claims_by_code["S2-BOUNDARY"]["claim_type"] == "scope"
    assert claims_by_code["S2-BOUNDARY"]["support_status"] == "supported"
    assert "Maverick Manufacturing (synthetic)" in claims_by_code["S2-BOUNDARY"]["rendered_text"]
    assert claims_by_code["S2-TOTAL"]["claim_type"] == "numeric"
    assert claims_by_code["S2-TOTAL"]["support_status"] == "supported"
    assert claims_by_code["S2-TOTAL"]["fact_binding_id"] is not None
    assert "86000" in claims_by_code["S2-TOTAL"]["rendered_text"]
    assert claims_by_code["S2-PRIOR-PERIOD"]["support_status"] == "unsupported"
    assert claims_by_code["S2-PRIOR-PERIOD"]["rendered_text"] is None
    assert validated_draft["gaps"][0]["code"].endswith("COMPARABLE_PRIOR_PERIOD_MISSING")

    validation_replay_response = await api_client.post(
        f"/api/assurance/drafts/{draft_id}/validate",
        json=validation_payload,
    )
    assert validation_replay_response.status_code == 200, validation_replay_response.text
    validation_replay = validation_replay_response.json()
    assert validation_replay["idempotent"] is True
    assert validation_replay["draft"]["payload_hash"] == validated_draft["payload_hash"]
    assert [item["id"] for item in validation_replay["draft"]["claims"]] == [
        item["id"] for item in validated_draft["claims"]
    ]

    evidence_pack_response = await api_client.get(
        f"/api/assurance/drafts/{draft_id}/evidence-pack",
        params=company_query,
    )
    assert evidence_pack_response.status_code == 200, evidence_pack_response.text
    evidence_pack = evidence_pack_response.json()
    assert evidence_pack["draft_id"] == draft_id
    assert evidence_pack["standard_code"] == "GHG-PROTOCOL-SCOPE-2-DEMO"
    assert len(evidence_pack["claims"]) == 3
    assert len(evidence_pack["gaps"]) == 1
    assert len(evidence_pack["fact_bindings"]) == 1
    assert evidence_pack["fact_bindings"][0]["placeholder"] == "fact_scope2_total"
    assert evidence_pack["fact_bindings"][0]["ledger_event_id"] == str(
        ids.measurement_ledger_event_id
    )
    assert [item["id"] for item in evidence_pack["evidence"]] == [str(ids.assurance_evidence_id)]
    assert evidence_pack["disclaimer"] == ("POC draft; not an assurance opinion or filing.")
    assert "content_text" not in evidence_pack_response.text

    wrong_tenant_pack = await api_client.get(
        f"/api/assurance/drafts/{draft_id}/evidence-pack",
        params={"company_id": str(other_company_id)},
    )
    assert wrong_tenant_pack.status_code == 404
    assert wrong_tenant_pack.json()["detail"]["code"] == "assurance_not_found"

    async with e2e_context.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(DisclosureDraft)) == 1
        assert await session.scalar(select(func.count()).select_from(DisclosureClaim)) == 3
        assert await session.scalar(select(func.count()).select_from(EvidenceGap)) == 1
        assert await session.scalar(select(func.count()).select_from(FactBinding)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Approval)
                .where(Approval.target_type == "disclosure_draft")
            )
            == 0
        )
