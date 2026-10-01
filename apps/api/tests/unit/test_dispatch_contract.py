from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.db.models.dispatch import DispatchScenario
from app.db.models.ledger import LedgerEvent
from app.modules.dispatch.errors import DispatchConflictError, DispatchValidationError
from app.modules.dispatch.optimization import CandidateWindow
from app.modules.dispatch.schemas import (
    CreateDispatchScenarioRequest,
    FrozenLoadSnapshot,
    FrozenMethodSnapshot,
    RejectedWindowView,
)
from app.modules.dispatch.service import (
    APPROVAL_TTL,
    DispatchService,
    _forecast_point_payload,
    _freeze_load,
    _freeze_method,
    _freeze_policy,
    _json_bytes,
    _persisted_forecast_intensity,
    raw_synthetic,
)


def _request() -> CreateDispatchScenarioRequest:
    return CreateDispatchScenarioRequest(
        company_id=UUID("00000000-0000-0000-0000-000000000001"),
        site_id=UUID("00000000-0000-0000-0000-000000000002"),
        flexible_load_id=UUID("00000000-0000-0000-0000-000000000003"),
        method_definition_id=UUID("00000000-0000-0000-0000-000000000004"),
        forecast_source_document_id=UUID("00000000-0000-0000-0000-000000000005"),
        requested_by=UUID("00000000-0000-0000-0000-000000000006"),
        window_start=datetime(2026, 10, 1, 8, tzinfo=UTC),
        window_end=datetime(2026, 10, 1, 20, tzinfo=UTC),
        baseline_start=datetime(2026, 10, 1, 8, tzinfo=UTC),
        available_capacity_kw=Decimal(500),
    )


def _constraint(kind: str, configuration: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000010"),
        code=f"hard-{kind}",
        name=f"Hard {kind}",
        constraint_type=kind,
        is_hard=True,
        valid_from=None,
        valid_to=None,
        configuration=configuration,
        is_active=True,
    )


def test_forecast_provenance_hash_material_is_decimal_scale_independent() -> None:
    point = SimpleNamespace(
        provider="electricity_maps",
        zone="IN",
        forecast_for=datetime(2026, 10, 1, 8, tzinfo=UTC),
        issued_at=datetime(2026, 10, 1, 7, tzinfo=UTC),
        emission_factor_type="lifecycle",
        flow_traced=True,
        is_estimated=True,
        temporal_granularity="hourly",
        provider_metadata={
            "api_version": "v4",
            "estimation_method": "synthetic_fixture",
        },
    )
    encoded = set()
    for value in (Decimal(480), Decimal("480.0"), Decimal("480.000000000")):
        point.intensity_gco2e_per_kwh = value
        encoded.add(_json_bytes(_forecast_point_payload(point)))

    assert len(encoded) == 1
    assert b'"intensity_gco2e_per_kwh":"480"' in encoded.pop()
    assert _persisted_forecast_intensity(Decimal("1.1234567895")) == Decimal("1.123456790")


def test_hard_constraints_narrow_and_freeze_the_request() -> None:
    frozen = DispatchService._freeze_constraints(
        _request(),
        [
            _constraint(
                "availability",
                {
                    "earliest_start": "2026-10-01T08:00:00Z",
                    "latest_finish": "2026-10-01T20:00:00Z",
                },
            ),
            _constraint(
                "deadline",
                {
                    "baseline_start": "2026-10-01T08:00:00Z",
                    "maximum_delay_minutes": 240,
                },
            ),
            _constraint(
                "blackout",
                {"start": "2026-10-01T10:00:00Z", "end": "2026-10-01T11:00:00Z"},
            ),
            _constraint("power", {"available_capacity_kw": "500"}),
        ],
    )

    assert frozen.maximum_delay_minutes == 240
    assert [(item.start.hour, item.end.hour) for item in frozen.blackouts] == [(10, 11)]
    assert [item.available_capacity_kw for item in frozen.capacity_windows] == [
        Decimal(500),
        Decimal(500),
    ]
    # The default is finalized from the scenario analysis signature, not from
    # an incomplete pre-forecast request hash.
    assert frozen.approval_idempotency_key == ""


def test_unknown_hard_constraint_fails_closed() -> None:
    with pytest.raises(DispatchValidationError, match="not supported"):
        DispatchService._freeze_constraints(
            _request(),
            [_constraint("dependency", {"load": "another-load"})],
        )


def test_fixture_synthetic_marker_is_preserved() -> None:
    assert raw_synthetic({"synthetic": True}) is True
    assert raw_synthetic({"isSynthetic": True}) is True
    assert raw_synthetic({}) is False


@pytest.mark.parametrize("invalid_delay", [True, 4.9])
def test_scenario_maximum_delay_requires_an_exact_integer(invalid_delay: object) -> None:
    with pytest.raises(ValidationError):
        CreateDispatchScenarioRequest.model_validate(
            {**_request().model_dump(), "maximum_delay_minutes": invalid_delay}
        )


def test_deadline_constraint_rejects_non_integer_delay() -> None:
    with pytest.raises(DispatchValidationError, match="exact integer"):
        DispatchService._freeze_constraints(
            _request(),
            [_constraint("deadline", {"maximum_delay_minutes": 4.9})],
        )


def test_power_constraint_uses_model_validity_window() -> None:
    constraint = _constraint("power", {"available_capacity_kw": "450"})
    constraint.valid_from = datetime(2026, 10, 1, 11, tzinfo=UTC)
    constraint.valid_to = datetime(2026, 10, 1, 13, tzinfo=UTC)

    frozen = DispatchService._freeze_constraints(_request(), [constraint])

    assert frozen.capacity_windows[-1].start == constraint.valid_from
    assert frozen.capacity_windows[-1].end == constraint.valid_to


def test_exact_baseline_is_frozen_even_when_a_hard_blackout_blocks_it() -> None:
    frozen = DispatchService._freeze_constraints(
        _request(),
        [
            _constraint(
                "blackout",
                {"start": "2026-10-01T08:00:00Z", "end": "2026-10-01T09:00:00Z"},
            )
        ],
    )

    assert frozen.baseline_start == _request().baseline_start
    assert frozen.blackouts[0].start == frozen.baseline_start


def test_hard_availability_may_exclude_reference_baseline() -> None:
    frozen = DispatchService._freeze_constraints(
        _request(),
        [
            _constraint(
                "availability",
                {
                    "earliest_start": "2026-10-01T10:00:00Z",
                    "latest_finish": "2026-10-01T20:00:00Z",
                },
            )
        ],
    )

    assert frozen.baseline_start == datetime(2026, 10, 1, 8, tzinfo=UTC)
    assert frozen.earliest_start == datetime(2026, 10, 1, 10, tzinfo=UTC)


def test_changed_load_method_or_policy_marks_frozen_scenario_stale() -> None:
    load = SimpleNamespace(
        id=uuid4(),
        code="BATCH-PROCESS-7",
        name="Batch Process 7",
        power_kw=Decimal(500),
        duration_minutes=120,
        energy_kwh=Decimal(1000),
        minimum_power_kw=None,
        maximum_power_kw=Decimal(500),
        is_interruptible=False,
        is_active=True,
    )
    method = SimpleNamespace(
        id=uuid4(),
        key="dispatch.minimum_carbon",
        version="1.0.0",
        code_version="test",
        configuration={},
        is_active=True,
    )
    policy = SimpleNamespace(
        id=uuid4(),
        key="dispatch.advisory",
        version="1.0.0",
        configuration={"approval_required": True},
        is_active=True,
    )
    frozen = DispatchService._freeze_constraints(_request(), []).model_copy(
        update={
            "load_snapshot": _freeze_load(load),
            "method_snapshot": _freeze_method(method),
            "policy_snapshot": _freeze_policy(policy),
        }
    )
    record = SimpleNamespace(load=load, method=method, policy=policy)
    DispatchService._validate_dependency_snapshots(record, frozen)

    load.power_kw = Decimal(499)
    with pytest.raises(DispatchConflictError, match="changed after") as error:
        DispatchService._validate_dependency_snapshots(record, frozen)
    assert error.value.code == "dispatch_scenario_stale"


def test_default_approval_expiry_refreshes_but_explicit_expiry_fails_closed() -> None:
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    expired = DispatchService._freeze_constraints(_request(), []).model_copy(
        update={"approval_expires_at": now - timedelta(seconds=1)}
    )
    assert DispatchService._resolve_approval_expiry(expired, now=now) == now + APPROVAL_TTL

    explicit = expired.model_copy(update={"approval_expiry_is_default": False})
    with pytest.raises(DispatchConflictError) as error:
        DispatchService._resolve_approval_expiry(explicit, now=now)
    assert error.value.code == "dispatch_approval_expired"


class _NoFeasibleSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, item: object) -> None:
        if isinstance(item, LedgerEvent) and item.id is None:
            item.id = uuid4()
        self.added.append(item)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


class _NoFeasibleRepository:
    def __init__(self, session: _NoFeasibleSession) -> None:
        self.session = session
        self.forecast_event = LedgerEvent(id=uuid4())

    async def get_forecast_ledger_event(self, **_: object) -> LedgerEvent:
        return self.forecast_event

    async def get_no_feasible_event(self, **_: object) -> LedgerEvent | None:
        return next(
            (item for item in self.session.added if isinstance(item, LedgerEvent)),
            None,
        )


@pytest.mark.asyncio
async def test_no_feasible_result_persists_and_replays_exact_rejections() -> None:
    request = _request()
    frozen = DispatchService._freeze_constraints(request, []).model_copy(
        update={
            "load_snapshot": FrozenLoadSnapshot(
                id=request.flexible_load_id,
                code="BATCH-PROCESS-7",
                name="Batch Process 7",
                power_kw=Decimal(500),
                duration_minutes=120,
                energy_kwh=Decimal(1000),
                minimum_power_kw=None,
                maximum_power_kw=Decimal(500),
                is_interruptible=False,
                is_active=True,
                snapshot_hash="d" * 64,
            ),
            "method_snapshot": FrozenMethodSnapshot(
                id=request.method_definition_id,
                key="dispatch.minimum_carbon",
                version="1.0.0",
                code_version="test",
                configuration={},
                is_active=True,
                snapshot_hash="e" * 64,
            ),
        }
    )
    scenario = DispatchScenario(
        id=uuid4(),
        company_id=request.company_id,
        site_id=request.site_id,
        flexible_load_id=request.flexible_load_id,
        method_definition_id=request.method_definition_id,
        forecast_source_document_id=request.forecast_source_document_id,
        window_start=request.window_start,
        window_end=request.window_end,
        objective="minimum_carbon",
        constraint_snapshot=frozen.model_dump(mode="json"),
        forecast_snapshot=[
            {
                "point_hash": "a" * 64,
                "evidence_item_id": None,
                "source_document_checksum": "f" * 64,
            }
        ],
        analysis_signature="b" * 64,
        context_hash="c" * 64,
        status="draft",
    )
    record = SimpleNamespace(
        scenario=scenario,
        method=SimpleNamespace(
            id=request.method_definition_id,
            version="1.0.0",
            code_version="test",
        ),
    )
    rejected = [
        RejectedWindowView(
            start=request.window_start,
            end=request.window_start.replace(hour=10),
            reasons=["capacity_exceeded"],
        )
    ]
    session = _NoFeasibleSession()
    service = DispatchService(session)  # type: ignore[arg-type]
    service.repository = _NoFeasibleRepository(session)  # type: ignore[assignment]

    initial = await service._persist_no_feasible_result(
        record=record,
        frozen=frozen,
        baseline=CandidateWindow(
            start=request.baseline_start,
            end=request.baseline_start + timedelta(hours=2),
            emissions_kgco2e=Decimal("470.000000"),
        ),
        rejected_views=rejected,
    )
    replayed = await service._read_no_feasible_result(scenario)

    assert scenario.status == "no_feasible_window"
    assert session.commits == 1
    assert initial.model_dump() == replayed.model_dump()
    assert initial.evaluated_windows == 1
    assert initial.rejected_windows[0].reasons == ["capacity_exceeded"]
    event = next(item for item in session.added if isinstance(item, LedgerEvent))
    assert event.event_type == "dispatch_no_feasible_window"
    assert event.payload["analysis_signature"] == scenario.analysis_signature
    assert event.payload["baseline"]["emissions_kgco2e"] == "470.000000"
