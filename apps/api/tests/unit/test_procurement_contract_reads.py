from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.procurement.errors import ProcurementError
from app.modules.procurement.repository import SupplierRecord
from app.modules.procurement.service import ProcurementService


@pytest.mark.asyncio
async def test_supplier_catalog_is_tenant_scoped_and_projects_product_counts() -> None:
    company_id = uuid4()
    supplier_id = uuid4()
    created_at = datetime(2026, 10, 1, tzinfo=UTC)
    supplier = SimpleNamespace(
        id=supplier_id,
        supplier_code="RECYCLED-AL-01",
        name="Synthetic Circular Metals",
        country_code="IN",
        status="active",
        supplier_metadata={"risk": "low", "synthetic": True},
        created_at=created_at,
        updated_at=created_at,
    )
    repository = SimpleNamespace(
        list_suppliers=AsyncMock(
            return_value=(
                [SupplierRecord(supplier=supplier, product_count=3, active_product_count=2)],
                1,
            )
        )
    )
    service = ProcurementService(SimpleNamespace())  # type: ignore[arg-type]
    service.repository = repository

    result = await service.list_suppliers(
        company_id=company_id,
        country_code="IN",
        active_only=True,
        search="Circular",
        limit=25,
        offset=0,
    )

    repository.list_suppliers.assert_awaited_once_with(
        company_id=company_id,
        country_code="IN",
        active_only=True,
        search="Circular",
        limit=25,
        offset=0,
    )
    assert result.total == 1
    assert result.items[0].id == supplier_id
    assert result.items[0].risk == "low"
    assert result.items[0].product_count == 3
    assert result.items[0].active_product_count == 2


@pytest.mark.asyncio
async def test_scenario_recommendation_resolves_the_scenario_owned_artifact() -> None:
    company_id = uuid4()
    scenario_id = uuid4()
    recommendation = SimpleNamespace(id=uuid4(), company_id=company_id)
    expected = object()
    repository = SimpleNamespace(
        get_recommendation_for_scenario=AsyncMock(return_value=recommendation),
        get_scenario=AsyncMock(),
    )
    service = ProcurementService(SimpleNamespace())  # type: ignore[arg-type]
    service.repository = repository
    service._recommendation_detail = AsyncMock(return_value=expected)  # type: ignore[method-assign]

    result = await service.get_scenario_recommendation(
        company_id=company_id,
        scenario_id=scenario_id,
    )

    assert result is expected
    repository.get_recommendation_for_scenario.assert_awaited_once_with(
        company_id=company_id,
        scenario_id=scenario_id,
    )
    repository.get_scenario.assert_not_awaited()
    service._recommendation_detail.assert_awaited_once_with(recommendation)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    [
        (None, "procurement_scenario_not_found"),
        (object(), "recommendation_not_found"),
    ],
)
async def test_scenario_recommendation_distinguishes_missing_scenario_from_no_result(
    scenario: object | None,
    expected_code: str,
) -> None:
    company_id = uuid4()
    scenario_id = uuid4()
    repository = SimpleNamespace(
        get_recommendation_for_scenario=AsyncMock(return_value=None),
        get_scenario=AsyncMock(return_value=scenario),
    )
    service = ProcurementService(SimpleNamespace())  # type: ignore[arg-type]
    service.repository = repository

    with pytest.raises(ProcurementError) as error:
        await service.get_scenario_recommendation(
            company_id=company_id,
            scenario_id=scenario_id,
        )

    assert error.value.code == expected_code
    assert error.value.status_code == 404
    assert error.value.field_details[0]["value"] == str(scenario_id)
