import pytest

from app.main import app
from app.modules.integrations.electricity_maps import get_electricity_maps_client


@pytest.mark.asyncio
async def test_history_defaults_to_declared_synthetic_fixture_without_network(api_client, e2e_context):
    class NetworkForbidden:
        async def list_zones(self):
            raise AssertionError("Fixture mode cannot access the network")

        async def get_carbon_intensity_range(self, **kwargs):
            raise AssertionError("Fixture mode cannot access the network")

    app.dependency_overrides[get_electricity_maps_client] = NetworkForbidden
    ids = e2e_context.ids
    context = {"company_id": str(ids.company_id), "site_id": str(ids.site_id)}
    try:
        response = await api_client.post(
            "/api/measurement/grid/history/sync", params=context,
            json={"start": "2026-07-01T00:00:00Z", "end": "2026-07-01T02:00:00Z"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["inserted_points"] == 2
        latest = await api_client.get("/api/measurement/grid/latest", params=context)
        assert latest.status_code == 200, latest.text
        assert latest.json()["provenance"]["synthetic"] is True
        assert latest.json()["provenance"]["provider_mode"] == "fixture"
    finally:
        app.dependency_overrides.pop(get_electricity_maps_client, None)
