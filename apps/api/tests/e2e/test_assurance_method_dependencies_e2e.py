from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models.carbon import CalculationRun, CarbonMeasurement
from app.db.models.semantic import MethodDefinition
from app.modules.assurance.repository import AssuranceRepository
from tests.e2e.test_generic_approvals_e2e import _disclosure_preview


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expired_method", "failed_calculation"])
async def test_changed_calculation_invalidates_preview_and_blocks_new_disclosure(
    api_client, e2e_context, change,
):
    ids = e2e_context.ids
    preview = await _disclosure_preview(api_client, e2e_context)
    async with e2e_context.session_factory() as session, session.begin():
        measurement = await session.get(CarbonMeasurement, ids.assurance_measurement_id)
        run = await session.get(CalculationRun, measurement.calculation_run_id)
        method = await session.get(MethodDefinition, run.method_definition_id)
        if change == "expired_method":
            method.effective_to = date(2026, 6, 30)
        else:
            run.status = "failed"
    detail = await api_client.get(
        f"/api/approvals/{preview['id']}", params={"company_id": str(ids.company_id)},
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["preview_current"] is False
    decided = await api_client.post(f"/api/approvals/{preview['id']}/decision", json={
        "company_id": str(ids.company_id), "actor_id": str(ids.approver_id),
        "decision": "approve", "preview_hash": preview["preview_hash"],
    })
    assert decided.status_code == 409, decided.text
    created = await api_client.post("/api/assurance/drafts", json={
        "company_id": str(ids.company_id), "site_id": str(ids.site_id),
        "reporting_period_id": str(ids.reporting_period_id),
        "standard_id": str(ids.assurance_standard_id),
        "measurement_id": str(ids.assurance_measurement_id),
        "requested_by": str(ids.analyst_id),
        "idempotency_key": str(uuid4()),
    })
    assert created.status_code == 422, created.text
    assert created.json()["detail"]["code"] == "assurance_validation_failed"


@pytest.mark.asyncio
async def test_renaming_current_method_does_not_hide_frozen_scope2_coverage(e2e_context):
    ids = e2e_context.ids
    async with e2e_context.session_factory() as session, session.begin():
        measurement = await session.get(CarbonMeasurement, ids.assurance_measurement_id)
        run = await session.get(CalculationRun, measurement.calculation_run_id)
        method = await session.get(MethodDefinition, run.method_definition_id)
        run.summary = {
            "input_snapshot": {
                "method_key": "measurement.scope2.location_based.hourly",
                "method_definition_id": str(method.id),
                "method_version": method.version,
                "code_version": method.code_version,
                "method_configuration": method.configuration,
                "coverage": {"full_reporting_period": False},
            },
        }
        method.key = "renamed-current-method"
    async with e2e_context.session_factory() as session:
        dependencies = await AssuranceRepository(session).load_draft_dependencies(
            company_id=ids.company_id, standard_id=ids.assurance_standard_id,
            site_id=ids.site_id, reporting_period_id=ids.reporting_period_id,
            measurement_id=ids.assurance_measurement_id,
            requested_by=ids.analyst_id,
            agent_run_id=None,
        )
        assert dependencies.measurement_coverage == {"full_reporting_period": False}
        assert dependencies.measurement_calculation["method_key"] == "renamed-current-method"
        assert dependencies.measurement_calculation["frozen_method"]["method_key"] == (
            "measurement.scope2.location_based.hourly"
        )
        run = await session.scalar(select(CalculationRun).where(
            CalculationRun.id == dependencies.measurement.calculation_run_id,
        ))
        assert run.summary["input_snapshot"]["method_key"] != "renamed-current-method"
