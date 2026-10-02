"""Upload real synthetic source rows for fresh-input domain and agent journeys."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import func, select

from app.db.models.carbon import ActivityRecord, CarbonMeasurement, RawActivityRecord
from app.modules.demo.fixtures import ACTIVITY_METRIC_ID, ELECTRICITY_ACTIVITY_METRIC_ID
from app.modules.demo.service import reset_and_seed_demo
from tests.e2e.conftest import E2EContext

FIXTURE_DIRECTORY = Path(__file__).resolve().parents[4] / "data" / "demo"


@dataclass(frozen=True)
class FreshQuarterInputs:
    context: dict[str, str]
    electricity_activity_ids: list[str]
    material_activity_ids: list[str]
    electricity_document_id: str
    material_document_id: str
    manifest: dict[str, Any]


async def prepare_complete_quarter_inputs(
    client: httpx.AsyncClient,
    e2e_context: E2EContext,
) -> FreshQuarterInputs:
    """Seed only the canonical foundation, then import both source files by HTTP.

    The E2E context owns a disposable cluster. No measurement, calculation, grid
    point, disclosure draft, or recommendation is pre-created by this helper.
    """
    async with e2e_context.session_factory() as session:
        seeded = await reset_and_seed_demo(session)
        assert await session.scalar(select(func.count()).select_from(CarbonMeasurement)) == 0
    context = {
        "company_id": str(seeded.company_id),
        "site_id": str(seeded.site_id),
        "reporting_period_id": str(seeded.reporting_period_id),
    }
    manifest = json.loads((FIXTURE_DIRECTORY / "complete-q3-manifest-v1.json").read_text())
    uploaded = []
    for filename, metric_id, count, interval in (
        (
            "electricity-hourly-complete-q3-v1.csv",
            ELECTRICITY_ACTIVITY_METRIC_ID,
            manifest["hourly_intervals"],
            {"interval_start": manifest["interval_start"], "interval_end": manifest["interval_end"]},
        ),
        ("activity-material-complete-q3-v1.csv", ACTIVITY_METRIC_ID, 1, {}),
    ):
        response = await client.post(
            "/api/activities/import",
            json={
                **context,
                **interval,
                "metric_definition_id": str(metric_id),
                "source_name": f"Synthetic complete Q3 v1: {filename}",
                "filename": filename,
                "content_type": "text/csv",
                "content": (FIXTURE_DIRECTORY / filename).read_text(encoding="utf-8"),
                "is_synthetic": True,
                "idempotency_key": f"fresh-complete-q3-v1-{filename}",
            },
        )
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["accepted_count"] == count, result
        assert result["rejected_count"] == result["issue_count"] == 0, result
        async with e2e_context.session_factory() as session:
            ids = list(await session.scalars(
                select(ActivityRecord.id)
                .join(RawActivityRecord, ActivityRecord.raw_activity_record_id == RawActivityRecord.id)
                .where(RawActivityRecord.source_document_id == UUID(result["source_document_id"]))
                .order_by(RawActivityRecord.row_number)
            ))
        assert len(ids) == count
        uploaded.append(([str(value) for value in ids], result["source_document_id"]))
    return FreshQuarterInputs(
        context=context,
        electricity_activity_ids=uploaded[0][0],
        material_activity_ids=uploaded[1][0],
        electricity_document_id=uploaded[0][1],
        material_document_id=uploaded[1][1],
        manifest=manifest,
    )
