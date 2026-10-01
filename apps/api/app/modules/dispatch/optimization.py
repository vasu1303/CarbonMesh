from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

EMISSIONS_QUANTUM = Decimal("0.000001")
PERCENT_QUANTUM = Decimal("0.0001")
GRAMS_PER_KILOGRAM = Decimal(1000)


@dataclass(frozen=True, slots=True)
class ForecastValue:
    forecast_for: datetime
    intensity_gco2e_per_kwh: Decimal


@dataclass(frozen=True, slots=True)
class TimeWindow:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class CapacityWindow:
    available_capacity_kw: Decimal
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True, slots=True)
class DispatchInputs:
    power_kw: Decimal
    energy_kwh: Decimal
    duration_minutes: int
    earliest_start: datetime
    latest_finish: datetime
    baseline_start: datetime
    maximum_delay_minutes: int
    blackouts: tuple[TimeWindow, ...] = ()
    capacity_windows: tuple[CapacityWindow, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateWindow:
    start: datetime
    end: datetime
    emissions_kgco2e: Decimal


@dataclass(frozen=True, slots=True)
class RejectedWindow:
    start: datetime
    end: datetime
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    baseline: CandidateWindow | None
    recommended: CandidateWindow | None
    feasible_windows: tuple[CandidateWindow, ...]
    rejected_windows: tuple[RejectedWindow, ...]
    avoided_kgco2e: Decimal | None
    reduction_pct: Decimal | None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Dispatch datetimes must include a UTC offset.")
    return value.astimezone(UTC)


def _overlaps(left: TimeWindow, right: TimeWindow) -> bool:
    return left.start < right.end and right.start < left.end


def _capacity_for_interval(
    interval: TimeWindow,
    windows: tuple[CapacityWindow, ...],
) -> Decimal | None:
    applicable = [
        window.available_capacity_kw
        for window in windows
        if (window.start is None or window.start < interval.end)
        and (window.end is None or window.end > interval.start)
    ]
    return min(applicable) if applicable else None


def _emissions(
    points: list[ForecastValue],
    *,
    energy_kwh: Decimal,
    intervals: int,
) -> Decimal:
    energy_per_interval = energy_kwh / Decimal(intervals)
    grams = sum(
        (energy_per_interval * point.intensity_gco2e_per_kwh for point in points),
        start=Decimal(0),
    )
    return (grams / GRAMS_PER_KILOGRAM).quantize(
        EMISSIONS_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def optimize_dispatch(
    inputs: DispatchInputs,
    forecast: tuple[ForecastValue, ...],
) -> OptimizationResult:
    """Select the earliest minimum-emissions eligible window deterministically.

    The exact baseline is an immutable emissions reference, not a candidate
    feasibility fallback. Operationally feasible windows with emissions above
    that reference are explicitly rejected because this advisory workflow may
    only claim non-negative avoided emissions.
    """

    if inputs.duration_minutes <= 0 or inputs.duration_minutes % 60 != 0:
        raise ValueError("The flexible-load duration must be a positive whole number of hours.")
    if inputs.power_kw <= 0 or inputs.energy_kwh <= 0:
        raise ValueError("Flexible-load power and energy must be positive.")
    if inputs.maximum_delay_minutes < 0:
        raise ValueError("Maximum delay cannot be negative.")

    earliest_start = _utc(inputs.earliest_start)
    latest_finish = _utc(inputs.latest_finish)
    baseline_start = _utc(inputs.baseline_start)
    if earliest_start >= latest_finish:
        raise ValueError("The allowed dispatch window is empty.")
    if any(
        value.minute != 0 or value.second != 0 or value.microsecond != 0
        for value in (earliest_start, latest_finish, baseline_start)
    ):
        raise ValueError("Dispatch window boundaries must align to whole UTC hours.")
    if any(_utc(item.start) >= _utc(item.end) for item in inputs.blackouts):
        raise ValueError("Blackout windows must have positive duration.")
    if any(
        item.available_capacity_kw < 0
        or (item.start is not None and item.end is not None and _utc(item.start) >= _utc(item.end))
        for item in inputs.capacity_windows
    ):
        raise ValueError("Capacity windows are invalid.")

    duration = timedelta(minutes=inputs.duration_minutes)
    interval_count = inputs.duration_minutes // 60
    latest_start = min(
        latest_finish - duration,
        baseline_start + timedelta(minutes=inputs.maximum_delay_minutes),
    )
    point_map: dict[datetime, ForecastValue] = {}
    for point in forecast:
        timestamp = _utc(point.forecast_for)
        if timestamp in point_map:
            raise ValueError("Forecast timestamps must be unique.")
        if point.intensity_gco2e_per_kwh < 0:
            raise ValueError("Forecast intensity cannot be negative.")
        point_map[timestamp] = ForecastValue(timestamp, point.intensity_gco2e_per_kwh)

    baseline_points = [
        point_map.get(baseline_start + timedelta(hours=index))
        for index in range(interval_count)
    ]
    if any(point is None for point in baseline_points):
        raise ValueError(
            "The exact frozen baseline requires complete forecast intervals."
        )
    baseline = CandidateWindow(
        start=baseline_start,
        end=baseline_start + duration,
        emissions_kgco2e=_emissions(
            [point for point in baseline_points if point is not None],
            energy_kwh=inputs.energy_kwh,
            intervals=interval_count,
        ),
    )

    candidate_starts: list[datetime] = []
    candidate_start = earliest_start
    while candidate_start <= latest_start:
        candidate_starts.append(candidate_start)
        candidate_start += timedelta(hours=1)
    feasible: list[CandidateWindow] = []
    rejected: list[RejectedWindow] = []
    for start in candidate_starts:
        end = start + duration
        candidate = TimeWindow(start, end)
        reasons: list[str] = []
        points: list[ForecastValue] = []
        for index in range(interval_count):
            interval_start = start + timedelta(hours=index)
            point = point_map.get(interval_start)
            if point is None:
                reasons.append("missing_forecast_interval")
                break
            points.append(point)

        if any(_overlaps(candidate, blackout) for blackout in inputs.blackouts):
            reasons.append("blackout_overlap")

        for index in range(interval_count):
            interval_start = start + timedelta(hours=index)
            interval = TimeWindow(interval_start, interval_start + timedelta(hours=1))
            capacity = _capacity_for_interval(interval, inputs.capacity_windows)
            if capacity is not None and inputs.power_kw > capacity:
                reasons.append("capacity_exceeded")
                break

        if reasons:
            rejected.append(RejectedWindow(start, end, tuple(dict.fromkeys(reasons))))
            continue
        candidate_window = CandidateWindow(
            start=start,
            end=end,
            emissions_kgco2e=_emissions(
                points,
                energy_kwh=inputs.energy_kwh,
                intervals=interval_count,
            ),
        )
        if candidate_window.emissions_kgco2e > baseline.emissions_kgco2e:
            rejected.append(
                RejectedWindow(
                    start,
                    end,
                    ("higher_emissions_than_baseline",),
                )
            )
            continue
        feasible.append(candidate_window)

    recommended = min(feasible, key=lambda item: (item.emissions_kgco2e, item.start), default=None)
    if recommended is None:
        return OptimizationResult(
            baseline=baseline,
            recommended=None,
            feasible_windows=(),
            rejected_windows=tuple(rejected),
            avoided_kgco2e=None,
            reduction_pct=None,
        )

    avoided = (baseline.emissions_kgco2e - recommended.emissions_kgco2e).quantize(
        EMISSIONS_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    reduction = (
        Decimal(0)
        if baseline.emissions_kgco2e == 0
        else (avoided / baseline.emissions_kgco2e * Decimal(100)).quantize(
            PERCENT_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    )
    return OptimizationResult(
        baseline=baseline,
        recommended=recommended,
        feasible_windows=tuple(feasible),
        rejected_windows=tuple(rejected),
        avoided_kgco2e=avoided,
        reduction_pct=reduction,
    )
