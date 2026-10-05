import re
from datetime import date
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from app.db.models.core import Actor, Company, ReportingPeriod, Site
from app.modules.workspace.repository import UUID_PATTERN
from app.modules.workspace.schemas import WorkspaceOptionKind

URL = "/api/workspace/options"


async def options(client, company_id, kind, **kwargs):
    response = await client.get(
        URL,
        params={
            "company_id": str(company_id),
            "kind": kind,
            **kwargs,
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    return response.json()


@pytest.mark.asyncio
async def test_all_kinds_use_real_tenant_scoped_queries(api_client, e2e_context):
    ids = e2e_context.ids
    other_company, other_site, other_period = uuid4(), uuid4(), uuid4()
    async with e2e_context.session_factory() as session, session.begin():
        session.add(
            Company(
                id=other_company,
                code="OTHER-SYNTHETIC",
                name="Other synthetic tenant",
                is_synthetic=True,
            )
        )
        await session.flush()
        session.add_all(
            [
                Site(
                    id=other_site,
                    company_id=other_company,
                    code="OTHER",
                    name="Other synthetic site",
                    country_code="IN",
                ),
                ReportingPeriod(
                    id=other_period,
                    company_id=other_company,
                    name="Q4 2026",
                    start_date=date(2026, 10, 1),
                    end_date=date(2026, 12, 31),
                ),
            ]
        )

    populated = set()
    for kind in WorkspaceOptionKind:
        listed = await options(api_client, ids.company_id, kind)
        assert listed["limit"] == 50 and listed["offset"] == 0
        assert listed["total"] >= len(listed["items"])
        if listed["items"]:
            populated.add(kind)
        for item in listed["items"]:
            assert set(item) == {"id", "label", "description", "status", "role"}
            assert UUID(item["id"])
            assert item["label"] and not re.search(UUID_PATTERN, item["label"], re.IGNORECASE)
            foreign_selection = await options(api_client, other_company, kind, id=item["id"])
            assert foreign_selection["total"] == 0 and foreign_selection["items"] == []
        for scopes in ({"site_id": str(other_site)}, {"reporting_period_id": str(other_period)}):
            empty = await options(api_client, ids.company_id, kind, **scopes)
            assert empty["items"] == [] and empty["total"] == 0
        scoped = await options(
            api_client,
            ids.company_id,
            kind,
            site_id=str(ids.site_id),
            reporting_period_id=str(ids.reporting_period_id),
        )
        assert scoped["total"] <= listed["total"]
        assert (await options(api_client, other_company, kind))["total"] == 0
    assert {
        "actors",
        "metrics",
        "methods",
        "policies",
        "documents",
        "evidence",
        "activity",
        "measurements",
        "suppliers",
        "products",
        "standards",
        "loads",
        "runs",
        "ledger",
    } <= populated


@pytest.mark.asyncio
async def test_paging_search_role_and_selected_record_lookup(api_client, e2e_context):
    company_id = e2e_context.ids.company_id
    actor_ids = sorted([uuid4() for _ in range(3)])
    foreign_company, foreign_actor = uuid4(), uuid4()
    async with e2e_context.session_factory() as session, session.begin():
        session.add(
            Company(
                id=foreign_company,
                code="FOREIGN-SYNTHETIC",
                name="Foreign synthetic tenant",
                is_synthetic=True,
            )
        )
        await session.flush()
        session.add(
            Actor(
                id=foreign_actor,
                company_id=foreign_company,
                display_name="Synthetic Picker 100%_match",
                role="approver",
                email="foreign@synthetic.invalid",
            )
        )
        for index, actor_id in enumerate(actor_ids):
            session.add(
                Actor(
                    id=actor_id,
                    company_id=company_id,
                    display_name="Synthetic Picker 100%_match",
                    role="approver",
                    email=f"picker-{index}@synthetic.invalid",
                )
            )

    filters = {"search": "pICKER 100%_", "role": "approver"}
    pages = [
        await options(api_client, company_id, "actors", limit=1, offset=i, **filters)
        for i in range(4)
    ]
    assert [page["total"] for page in pages] == [3, 3, 3, 3]
    assert [page["items"][0]["id"] for page in pages[:3]] == [str(id_) for id_ in actor_ids]
    assert pages[3]["items"] == []
    assert (await options(api_client, company_id, "actors", search="100%Z"))["total"] == 0
    selected = await options(
        api_client,
        company_id,
        "actors",
        id=str(actor_ids[2]),
        offset=100,
        search="does not match",
        role="approver",
    )
    assert selected["total"] == 1 and selected["offset"] == 0
    assert selected["items"][0]["id"] == str(actor_ids[2])
    assert selected["items"][0]["role"] == "approver"
    assert (await options(api_client, company_id, "actors", id=str(actor_ids[0]), role="auditor"))[
        "total"
    ] == 0
    assert (await options(api_client, company_id, "actors", id=str(foreign_actor)))["total"] == 0
    assert (await options(api_client, uuid4(), "actors"))["total"] == 0


@pytest.mark.asyncio
async def test_scoped_measurements_and_uuid_free_labels(api_client, e2e_context):
    ids = e2e_context.ids
    site_id, period_id, actor_id = uuid4(), uuid4(), uuid4()
    async with e2e_context.session_factory() as session, session.begin():
        session.add_all(
            [
                Site(
                    id=site_id,
                    company_id=ids.company_id,
                    code="EMPTY",
                    name="Empty synthetic site",
                    country_code="IN",
                ),
                ReportingPeriod(
                    id=period_id,
                    company_id=ids.company_id,
                    name="Q4 2026",
                    start_date=date(2026, 10, 1),
                    end_date=date(2026, 12, 31),
                ),
                Actor(
                    id=actor_id,
                    company_id=ids.company_id,
                    display_name=f"Synthetic actor {actor_id}",
                    role="auditor",
                    email="uuid-label@synthetic.invalid",
                ),
            ]
        )
    for scope in ({"site_id": str(site_id)}, {"reporting_period_id": str(period_id)}):
        result = await options(api_client, ids.company_id, "measurements", **scope)
        assert result["total"] == 0
        selected = await options(
            api_client, ids.company_id, "measurements", id=str(ids.measurement_id), **scope
        )
        assert selected["total"] == 0
    result = await options(api_client, ids.company_id, "actors", id=str(actor_id))
    assert result["items"][0]["label"] == "Synthetic actor"


@pytest.mark.asyncio
async def test_forecast_picker_returns_one_snapshot_and_stored_reads_work(
    api_client,
    e2e_context,
    monkeypatch,
):
    ids = e2e_context.ids
    network = Mock(side_effect=AssertionError("Unexpected live provider call"))
    monkeypatch.setattr("app.modules.integrations.electricity_maps.urlopen", network)
    request = {"company_id": str(ids.company_id), "site_id": str(ids.site_id)}
    disabled = await api_client.post("/api/dispatch/forecasts/sync", json=request)
    assert disabled.status_code == 503, disabled.text
    assert disabled.json()["detail"]["code"] == "integration_live_disabled"
    assert (await options(api_client, ids.company_id, "forecasts"))["total"] == 0

    synced = await api_client.post(
        "/api/dispatch/forecasts/sync", json={**request, "source_mode": "fixture"}
    )
    assert synced.status_code == 200, synced.text
    result = await options(api_client, ids.company_id, "forecasts", site_id=str(ids.site_id))
    assert result["total"] == 1
    assert result["items"][0]["id"] == synced.json()["source_document_id"]
    assert "IN forecast" in result["items"][0]["label"]
    history_request = {"start": "2026-07-01T00:00:00Z", "end": "2026-07-01T02:00:00Z"}
    disabled_history = await api_client.post(
        "/api/measurement/grid/history/sync",
        params=request,
        json=history_request,
    )
    assert disabled_history.status_code == 503, disabled_history.text
    assert disabled_history.json()["detail"]["code"] == "integration_live_disabled"
    history = await api_client.post(
        "/api/measurement/grid/history/sync",
        params=request,
        json={**history_request, "mode": "fixture"},
    )
    assert history.status_code == 200, history.text
    latest = await api_client.get("/api/measurement/grid/latest", params=request)
    assert latest.status_code == 200, latest.text
    measurement = await api_client.get(
        f"/api/measurements/{ids.measurement_id}", params={"company_id": str(ids.company_id)}
    )
    assert measurement.status_code == 200, measurement.text
    network.assert_not_called()


@pytest.mark.asyncio
async def test_typed_validation_and_credential_free_workspace_access(
    api_client, e2e_context, monkeypatch
):
    ids = e2e_context.ids
    valid = {"company_id": str(ids.company_id), "kind": "actors"}
    for invalid in (
        {"company_id": "invalid"},
        {"site_id": "invalid"},
        {"reporting_period_id": "invalid"},
        {"kind": "unknown"},
        {"id": "invalid"},
        {"role": "owner"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"offset": 10001},
        {"search": "x" * 201},
        {"kind": "measurements", "role": "approver"},
    ):
        response = await api_client.get(URL, params={**valid, **invalid})
        assert response.status_code == 422, response.text
    assert (await api_client.get(URL, params={"kind": "actors"})).status_code == 422
    assert (
        await api_client.get(URL, params={"company_id": str(ids.company_id)})
    ).status_code == 422

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    accessible = await api_client.get(URL, params=valid)
    assert accessible.status_code == 200, accessible.text
    assert accessible.json()["total"] > 0
    assert "set-cookie" not in accessible.headers
    missing_company = await api_client.get(
        URL, params={**valid, "company_id": str(uuid4())}
    )
    assert missing_company.status_code == 200, missing_company.text
    assert missing_company.json()["items"] == []
    assert missing_company.json()["total"] == 0
