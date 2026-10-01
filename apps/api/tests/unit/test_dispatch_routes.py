from app.api.routes.dispatch import router
from app.main import app
from app.modules.dispatch.schemas import ForecastSyncRequest


def test_dispatch_router_exposes_only_the_five_canonical_operations() -> None:
    paths = {(route.path, next(iter(route.methods))) for route in router.routes}

    assert paths == {
        ("/dispatch/loads", "GET"),
        ("/dispatch/forecasts/sync", "POST"),
        ("/dispatch/scenarios", "POST"),
        ("/dispatch/scenarios/{scenario_id}/optimize", "POST"),
        ("/dispatch/scenarios/{scenario_id}/recommendation", "GET"),
    }


def test_forecast_sync_defaults_to_credential_free_fixture_mode() -> None:
    fields = ForecastSyncRequest.model_fields

    assert fields["source_mode"].default == "fixture"
    assert fields["horizon_hours"].default == 24


def test_all_dispatch_operations_are_mounted_under_the_canonical_api_prefix() -> None:
    paths = app.openapi()["paths"]

    assert {
        ("get", "/api/dispatch/loads"),
        ("post", "/api/dispatch/forecasts/sync"),
        ("post", "/api/dispatch/scenarios"),
        ("post", "/api/dispatch/scenarios/{scenario_id}/optimize"),
        ("get", "/api/dispatch/scenarios/{scenario_id}/recommendation"),
    } <= {
        (method, path)
        for path, operations in paths.items()
        for method in operations
    }
