from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.db.models.carbon import ActivityRecord, DataQualityIssue, RawActivityRecord
from app.db.models.core import AuditLog, DataSource, SourceDocument
from app.db.models.semantic import MetricDefinition
from app.modules.demo.fixtures import (
    ELECTRICITY_ACTIVITY_METRIC_ID,
    ELECTRICITY_DOCUMENT_ID,
    ELECTRICITY_SOURCE_ID,
)
from app.modules.demo.service import reset_and_seed_demo
from app.modules.imports.repository import ImportRepository
from tests.e2e.conftest import E2EContext


async def _electricity_request(e2e_context: E2EContext, content, **overrides):
    ids = e2e_context.ids
    async with e2e_context.session_factory() as session:
        metric_id = await session.scalar(
            select(MetricDefinition.id).where(
                MetricDefinition.company_id == ids.company_id,
                MetricDefinition.key == "activity.electricity_consumption",
                MetricDefinition.version == "1.0.0",
            )
        )
    assert metric_id is not None
    return {
        "company_id": str(ids.company_id),
        "site_id": str(ids.site_id),
        "reporting_period_id": str(ids.reporting_period_id),
        "metric_definition_id": str(metric_id),
        "source_name": "Synthetic hourly meter data",
        "filename": "synthetic-electricity.json",
        "content_type": "application/json",
        "content": content,
        "is_synthetic": True,
        **overrides,
    }


@pytest.mark.asyncio
async def test_hourly_import_replay_conflict_and_raw_decimal_provenance(
    api_client,
    e2e_context: E2EContext,
) -> None:
    payload = [
        {"timestamp": "2026-09-01T05:30:00+05:30", "kwh": "10.123456"},
        {"timestamp": "2026-09-01T01:00Z", "quantity": "0.25", "unit": "MWh"},
    ]
    request = await _electricity_request(
        e2e_context,
        payload,
        idempotency_key="hourly-test-command",
    )
    first, repeat = await asyncio.gather(
        *(api_client.post("/api/activities/import", json=request) for _ in range(2))
    )
    assert first.status_code == repeat.status_code == 201, (first.text, repeat.text)
    assert first.json()["import_id"] == repeat.json()["import_id"]
    assert first.json()["accepted_count"] == 2
    assert first.json()["issue_count"] == 0
    import_id = UUID(first.json()["import_id"])

    conflicting = await api_client.post(
        "/api/activities/import",
        json={**request, "filename": "different.json"},
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["detail"]["code"] == "import_idempotency_conflict"

    async with e2e_context.session_factory() as session:
        source_count = await session.scalar(
            select(func.count(DataSource.id)).where(
                DataSource.configuration["idempotency_key"].astext == "hourly-test-command"
            )
        )
        rows = (
            await session.execute(
                select(RawActivityRecord, ActivityRecord)
                .join(ActivityRecord, ActivityRecord.raw_activity_record_id == RawActivityRecord.id)
                .where(RawActivityRecord.data_source_id == import_id)
                .order_by(RawActivityRecord.row_number)
            )
        ).all()
    assert source_count == 1
    assert [raw.raw_payload for raw, _ in rows] == payload
    assert [activity.normalized_quantity for _, activity in rows] == [
        Decimal("10.123456"),
        Decimal("250.000000"),
    ]

    duplicate = await api_client.post(
        "/api/activities/import",
        json={
            **request,
            "idempotency_key": "another-upload",
            "content": [{"row_key": "new-key", "timestamp": "2026-09-01T00:00Z", "kwh": "20"}],
        },
    )
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["accepted_count"] == 0
    assert duplicate.json()["issues"][0]["code"] == "duplicate_timestamp"


@pytest.mark.asyncio
async def test_hourly_import_quality_review_is_tenant_scoped_audited_and_fail_closed(
    api_client,
    e2e_context: E2EContext,
    monkeypatch,
) -> None:
    ids = e2e_context.ids
    request = await _electricity_request(
        e2e_context,
        [
            {"row_key": "a", "timestamp": "2026-09-01T00:00Z", "kwh": "10"},
            {"row_key": "b", "timestamp": "2026-09-01T05:30+05:30", "kwh": "11"},
            {"row_key": "c", "timestamp": "2026-09-01T02:00Z", "kwh": "12"},
        ],
    )
    imported = await api_client.post("/api/activities/import", json=request)
    assert imported.status_code == 201, imported.text
    result = imported.json()
    assert result["accepted_count"] == 1
    assert result["rejected_count"] == 2
    issue = next(item for item in result["issues"] if item["code"] == "duplicate_timestamp")
    path = f"/api/quality/issues/{issue['id']}"
    decision = {
        "company_id": str(ids.company_id),
        "actor_id": str(ids.analyst_id),
        "status": "resolved",
        "decision_note": "Reviewed rejected synthetic source rows; data remains excluded.",
    }
    wrong_tenant = await api_client.patch(path, json={**decision, "company_id": str(uuid4())})
    assert wrong_tenant.status_code == 404
    wrong_role = await api_client.patch(
        path,
        json={**decision, "actor_id": str(ids.procurement_manager_id)},
    )
    assert wrong_role.status_code == 403
    waived_error = await api_client.patch(path, json={**decision, "status": "waived"})
    assert waived_error.status_code == 409

    resolved = await api_client.patch(path, json=decision, headers={"X-Trace-ID": "quality-test"})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["details"]["blocks_verification"] is True
    assert resolved.json()["details"]["data_validity_unchanged"] is True
    retry = await api_client.patch(path, json=decision)
    assert retry.status_code == 200
    assert retry.json() == resolved.json()
    changed = await api_client.patch(path, json={**decision, "decision_note": "Changed decision"})
    assert changed.status_code == 409
    async with e2e_context.session_factory() as session:
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_type == "data_quality_issue",
                        AuditLog.entity_id == UUID(issue["id"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        raw = await session.get(RawActivityRecord, UUID(issue["raw_activity_record_id"]))
    assert len(audits) == 1
    assert audits[0].actor_id == ids.analyst_id
    assert audits[0].trace_id == "quality-test"
    assert raw.import_status == "rejected"

    warning_id = uuid4()
    async with e2e_context.session_factory() as session, session.begin():
        session.add(
            DataQualityIssue(
                id=warning_id,
                company_id=ids.company_id,
                issue_type="review",
                code="synthetic_review_warning",
                severity="warning",
                status="open",
                message="Synthetic warning for a human review.",
                details={},
            )
        )
    warning_decision = {
        **decision,
        "status": "waived",
        "decision_note": "Reviewed optional warning.",
    }

    async def fail_audit(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(ImportRepository, "create_audit_log", fail_audit)
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            await api_client.patch(f"/api/quality/issues/{warning_id}", json=warning_decision)
    async with e2e_context.session_factory() as session:
        unchanged = await session.get(DataQualityIssue, warning_id)
    assert unchanged.status == "open"
    assert unchanged.details == {}
    waived_warning = await api_client.patch(
        f"/api/quality/issues/{warning_id}",
        json=warning_decision,
    )
    assert waived_warning.status_code == 200, waived_warning.text
    assert waived_warning.json()["status"] == "waived"


@pytest.mark.asyncio
async def test_hourly_csv_import_rejects_invalid_timestamp_and_boundary_gaps(
    api_client,
    e2e_context: E2EContext,
) -> None:
    request = await _electricity_request(
        e2e_context,
        "timestamp,kwh,unit\n2026-09-01T01:00Z,12,kWh\n2026-09-01T02:00,13,kWh\n",
        content_type="text/csv",
        filename="synthetic-electricity.csv",
        interval_start="2026-09-01T00:00Z",
        interval_end="2026-09-01T03:00Z",
    )
    response = await api_client.post("/api/activities/import", json=request)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["accepted_count"] == 1
    assert result["rejected_count"] == 1
    assert [issue["code"] for issue in result["issues"]].count("missing_interval") == 2
    assert any(issue["field_name"] == "timestamp" for issue in result["issues"])


@pytest.mark.asyncio
async def test_reset_fixture_document_can_be_imported_once_without_checksum_collision(
    api_client, e2e_context: E2EContext,
) -> None:
    async with e2e_context.session_factory() as session:
        seeded = await reset_and_seed_demo(session)
    fixture = Path(__file__).resolve().parents[4] / "data/demo/electricity-hourly.csv"
    request = {
        "company_id": str(seeded.company_id),
        "site_id": str(seeded.site_id),
        "reporting_period_id": str(seeded.reporting_period_id),
        "metric_definition_id": str(ELECTRICITY_ACTIVITY_METRIC_ID),
        "source_name": "Synthetic Plant B hourly electricity import",
        "filename": fixture.name,
        "content_type": "text/csv",
        "content": fixture.read_text(encoding="utf-8"),
        "is_synthetic": True,
        "idempotency_key": "synthetic-demo-electricity-upload",
    }
    response = await api_client.post("/api/activities/import", json=request)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["import_id"] == str(ELECTRICITY_SOURCE_ID)
    assert result["source_document_id"] == str(ELECTRICITY_DOCUMENT_ID)
    assert result["status"] == "completed_with_errors"
    assert result["accepted_count"] == 2158
    assert result["rejected_count"] == 2
    assert {issue["code"] for issue in result["issues"]} >= {
        "duplicate_timestamp", "missing_interval",
    }
    repeated = await api_client.post("/api/activities/import", json=request)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json() == result
    duplicate = await api_client.post("/api/activities/import", json={
        **request, "idempotency_key": "second-demo-upload",
    })
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["accepted_count"] == 0
    assert duplicate.json()["issues"][0]["code"] == "duplicate_document"
    async with e2e_context.session_factory() as session:
        raw_count = await session.scalar(select(func.count(RawActivityRecord.id)).where(
            RawActivityRecord.source_document_id == ELECTRICITY_DOCUMENT_ID,
        ))
        document_count = await session.scalar(select(func.count(SourceDocument.id)).where(
            SourceDocument.data_source_id == ELECTRICITY_SOURCE_ID,
        ))
    assert raw_count == 2160
    assert document_count == 1
