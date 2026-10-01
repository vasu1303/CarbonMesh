from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.dispatch.optimization import (
    CapacityWindow,
    DispatchInputs,
    ForecastValue,
    TimeWindow,
    optimize_dispatch,
)

START = datetime(2026, 10, 1, 8, tzinfo=UTC)


def _forecast(values: list[int]) -> tuple[ForecastValue, ...]:
    return tuple(
        ForecastValue(
            forecast_for=START + timedelta(hours=index),
            intensity_gco2e_per_kwh=Decimal(value),
        )
        for index, value in enumerate(values)
    )


def _inputs(**changes: object) -> DispatchInputs:
    values: dict[str, object] = {
        "power_kw": Decimal(500),
        "energy_kwh": Decimal(1000),
        "duration_minutes": 120,
        "earliest_start": START,
        "latest_finish": START + timedelta(hours=12),
        "baseline_start": START,
        "maximum_delay_minutes": 240,
        "blackouts": (TimeWindow(START + timedelta(hours=2), START + timedelta(hours=3)),),
        "capacity_windows": (CapacityWindow(Decimal(500)),),
    }
    values.update(changes)
    return DispatchInputs(**values)  # type: ignore[arg-type]


def test_maverick_dispatch_golden_window_and_decimal_impact() -> None:
    result = optimize_dispatch(
        _inputs(),
        _forecast([480, 460, 420, 300, 280, 350, 442, 479, 501, 488, 470, 455]),
    )

    assert result.baseline is not None
    assert result.recommended is not None
    assert result.baseline.start == START
    assert result.baseline.end == START + timedelta(hours=2)
    assert result.baseline.emissions_kgco2e == Decimal("470.000000")
    assert result.recommended.start == START + timedelta(hours=3)
    assert result.recommended.end == START + timedelta(hours=5)
    assert result.recommended.emissions_kgco2e == Decimal("290.000000")
    assert result.avoided_kgco2e == Decimal("180.000000")
    assert result.reduction_pct == Decimal("38.2979")
    assert [(item.start, item.reasons) for item in result.rejected_windows] == [
        (START + timedelta(hours=1), ("blackout_overlap",)),
        (START + timedelta(hours=2), ("blackout_overlap",)),
    ]


def test_equal_emissions_use_earliest_feasible_start_as_tie_break() -> None:
    result = optimize_dispatch(
        _inputs(blackouts=()),
        _forecast([300] * 12),
    )

    assert result.recommended is not None
    assert result.recommended.start == START
    assert result.avoided_kgco2e == Decimal("0.000000")
    assert result.reduction_pct == Decimal(0)


def test_missing_interval_rejects_each_affected_enumerated_window_without_interpolation() -> None:
    forecast = tuple(
        point
        for point in _forecast([480, 460, 420, 300, 280, 350, 442])
        if point.forecast_for != START + timedelta(hours=3)
    )

    result = optimize_dispatch(_inputs(blackouts=()), forecast)

    rejected = {item.start: item.reasons for item in result.rejected_windows}
    assert rejected[START + timedelta(hours=2)] == ("missing_forecast_interval",)
    assert rejected[START + timedelta(hours=3)] == ("missing_forecast_interval",)
    assert result.recommended is not None


def test_capacity_can_produce_typed_no_feasible_result_without_relaxing_constraint() -> None:
    result = optimize_dispatch(
        _inputs(
            blackouts=(),
            capacity_windows=(CapacityWindow(Decimal(499)),),
        ),
        _forecast([300] * 12),
    )

    assert result.baseline is not None
    assert result.baseline.emissions_kgco2e == Decimal("300.000000")
    assert result.recommended is None
    assert result.feasible_windows == ()
    assert result.avoided_kgco2e is None
    assert {reason for item in result.rejected_windows for reason in item.reasons} == {
        "capacity_exceeded"
    }


def test_blocked_exact_baseline_remains_reference_and_is_not_substituted() -> None:
    result = optimize_dispatch(
        _inputs(
            blackouts=(TimeWindow(START, START + timedelta(hours=1)),),
            capacity_windows=(CapacityWindow(Decimal(500)),),
        ),
        _forecast([480, 460, 420, 300, 280, 350, 442]),
    )

    assert result.baseline is not None
    assert result.baseline.start == START
    assert result.baseline.emissions_kgco2e == Decimal("470.000000")
    assert result.recommended is not None
    assert result.recommended.start == START + timedelta(hours=3)


def test_baseline_can_extend_beyond_a_narrowed_candidate_window() -> None:
    result = optimize_dispatch(
        _inputs(
            latest_finish=START + timedelta(hours=1),
            maximum_delay_minutes=0,
            blackouts=(),
        ),
        _forecast([300, 300]),
    )

    assert result.baseline is not None
    assert result.baseline.end == START + timedelta(hours=2)
    assert result.recommended is None


def test_missing_baseline_forecast_fails_closed() -> None:
    with pytest.raises(ValueError, match="baseline requires complete forecast"):
        optimize_dispatch(
            _inputs(blackouts=()),
            tuple(
                point
                for point in _forecast([300] * 12)
                if point.forecast_for != START + timedelta(hours=1)
            ),
        )


def test_higher_emissions_windows_are_ineligible_for_lower_carbon_advice() -> None:
    result = optimize_dispatch(
        _inputs(
            earliest_start=START + timedelta(hours=2),
            blackouts=(),
            maximum_delay_minutes=240,
        ),
        _forecast([100, 100, 300, 300, 300, 300, 300]),
    )

    assert result.baseline is not None
    assert result.baseline.emissions_kgco2e == Decimal("100.000000")
    assert result.recommended is None
    assert result.feasible_windows == ()
    assert {reason for item in result.rejected_windows for reason in item.reasons} == {
        "higher_emissions_than_baseline"
    }


def test_non_hourly_duration_fails_closed() -> None:
    with pytest.raises(ValueError, match="whole number of hours"):
        optimize_dispatch(
            _inputs(duration_minutes=90),
            _forecast([300] * 12),
        )


def test_non_hour_aligned_windows_fail_closed() -> None:
    with pytest.raises(ValueError, match="whole UTC hours"):
        optimize_dispatch(
            _inputs(
                earliest_start=START + timedelta(minutes=30),
                baseline_start=START + timedelta(minutes=30),
            ),
            _forecast([300] * 12),
        )
