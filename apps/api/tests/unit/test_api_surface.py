from app.main import app


def test_requested_api_surface_is_registered() -> None:
    paths = app.openapi()["paths"]
    expected = {
        ("get", "/api/health"),
        ("post", "/api/demo/reset"),
        ("get", "/api/semantic/metrics"),
        ("post", "/api/context/resolve"),
        ("post", "/api/activities/import"),
        ("post", "/api/imports/suppliers"),
        ("get", "/api/imports/{import_id}"),
        ("get", "/api/quality/issues"),
        ("post", "/api/measurement/calculate"),
        ("get", "/api/measurements"),
        ("get", "/api/measurements/{measurement_id}"),
        ("post", "/api/agent/requests"),
        ("get", "/api/runs/{run_id}"),
        ("get", "/api/runs/{run_id}/events"),
        ("post", "/api/procurement/scenarios/{scenario_id}/score"),
        ("post", "/api/measurement/grid/history/sync"),
        ("get", "/api/measurement/grid/latest"),
        ("get", "/api/ledger/events"),
        ("get", "/api/ledger/events/{event_id}"),
    }
    registered = {
        (method, path)
        for path, operations in paths.items()
        for method in operations
    }

    assert expected <= registered


def test_grid_aliases_keep_company_and_site_scope_explicit() -> None:
    paths = app.openapi()["paths"]

    for method, path in (
        ("post", "/api/measurement/grid/history/sync"),
        ("get", "/api/measurement/grid/latest"),
    ):
        parameters = {
            (parameter["in"], parameter["name"]): parameter
            for parameter in paths[path][method]["parameters"]
        }

        assert parameters[("query", "company_id")]["required"] is True
        assert parameters[("query", "site_id")]["required"] is True


def test_scenario_score_uses_path_id_and_tenant_only_body() -> None:
    openapi = app.openapi()
    operation = openapi["paths"][
        "/api/procurement/scenarios/{scenario_id}/score"
    ]["post"]
    path_parameter = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "path" and parameter["name"] == "scenario_id"
    )
    body_reference = operation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    body_schema = openapi["components"]["schemas"][body_reference.rsplit("/", 1)[-1]]

    assert path_parameter["required"] is True
    assert body_schema["required"] == ["company_id"]
    assert set(body_schema["properties"]) == {"company_id"}


def test_obsolete_routes_are_not_registered() -> None:
    paths = app.openapi()["paths"]

    assert "/api/imports/activity" not in paths
    assert "/api/data-quality/issues" not in paths
    assert "/api/measurements/calculate" not in paths
    assert "/api/agent/query" not in paths
    assert "/api/procurement/assessments/run" not in paths
