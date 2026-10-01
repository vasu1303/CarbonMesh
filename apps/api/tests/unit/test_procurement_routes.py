from app.api.routes.procurement import router


def test_procurement_router_exposes_canonical_domain_relative_paths() -> None:
    paths = {(route.path, next(iter(route.methods))) for route in router.routes}

    assert paths == {
        ("/procurement/suppliers", "GET"),
        ("/procurement/products", "GET"),
        ("/procurement/scenarios/{scenario_id}/score", "POST"),
        ("/procurement/scenarios", "POST"),
        ("/procurement/scenarios/{scenario_id}", "GET"),
        ("/procurement/scenarios/{scenario_id}/recommendation", "GET"),
    }
