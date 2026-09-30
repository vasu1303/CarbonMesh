from app.main import app


def test_requested_v1_api_surface_is_registered() -> None:
    paths = app.openapi()["paths"]
    expected = {
        ("get", "/api/v1/health"),
        ("post", "/api/v1/demo/reset"),
        ("get", "/api/v1/semantic/metrics"),
        ("post", "/api/v1/context/resolve"),
        ("post", "/api/v1/imports/activity"),
        ("post", "/api/v1/imports/suppliers"),
        ("get", "/api/v1/imports/{import_id}"),
        ("get", "/api/v1/data-quality/issues"),
        ("post", "/api/v1/measurements/calculate"),
        ("get", "/api/v1/measurements"),
        ("get", "/api/v1/measurements/{measurement_id}"),
    }
    registered = {
        (method, path)
        for path, operations in paths.items()
        for method in operations
    }

    assert expected <= registered
