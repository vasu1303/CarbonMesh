from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.core import Approval, DataSource, EvidenceItem, SourceDocument
from app.db.models.dispatch import DispatchRecommendation, DispatchScenario, GridForecast
from app.db.models.ledger import FactBinding, LedgerEvent, LedgerEventEvidence, LineageEdge
from app.modules.dispatch.errors import (
    DispatchConflictError,
    DispatchError,
    DispatchNotFoundError,
    DispatchValidationError,
    ForecastProviderError,
)
from app.modules.dispatch.optimization import (
    CandidateWindow,
    DispatchInputs,
    ForecastValue,
    TimeWindow,
    optimize_dispatch,
)
from app.modules.dispatch.optimization import (
    CapacityWindow as OptimizationCapacityWindow,
)
from app.modules.dispatch.repository import DispatchRepository
from app.modules.dispatch.schemas import (
    ApprovalPreviewView,
    BlackoutWindow,
    CapacityWindow,
    CreateDispatchScenarioRequest,
    DispatchMethodView,
    DispatchOptimizationResult,
    DispatchRecommendationResult,
    DispatchRecommendationView,
    DispatchScenarioView,
    FlexibleLoadList,
    FlexibleLoadView,
    ForecastPointView,
    ForecastSyncRequest,
    ForecastSyncResult,
    FrozenDispatchConstraints,
    FrozenLoadSnapshot,
    FrozenMethodSnapshot,
    FrozenPolicySnapshot,
    OperatingConstraintView,
    OptimizeScenarioRequest,
    RejectedWindowView,
)
from app.modules.integrations.electricity_maps import (
    ElectricityMapsProvider,
    ElectricityMapsProviderError,
)
from app.modules.integrations.grid_forecast import fetch_normalized_grid_forecast
from app.modules.integrations.schemas import NormalizedGridForecast
from app.modules.ledger.service import normalize_json, payload_sha256

FORECAST_PROVIDER = "electricity_maps"
FORECAST_API_VERSION = "v4"
FORECAST_ENDPOINT = "/carbon-intensity/forecast"
EXPECTED_FORECAST_POINTS = 24
FORECAST_INTENSITY_QUANTUM = Decimal("0.000000001")
APPROVAL_TTL = timedelta(hours=24)
RATIONALE = (
    "Advisory only: prefer the complete feasible window with the lowest calculated "
    "grid emissions. A human decision is required and no equipment action is authorized."
)


def _canonical(value: Any) -> Any:
    return normalize_json(value)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonical(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _hash(value: Mapping[str, Any]) -> str:
    return payload_sha256(value)[1]


def _persisted_forecast_intensity(value: Decimal) -> Decimal:
    """Match PostgreSQL NUMERIC(24, 9) before hashing immutable provenance."""

    return value.quantize(FORECAST_INTENSITY_QUANTUM, rounding=ROUND_HALF_UP)


def _canonical_forecast_intensity(value: Decimal) -> Decimal:
    """Make numerically equal fixed-scale values hash to the same JSON bytes."""

    return _persisted_forecast_intensity(value).normalize()


def raw_synthetic(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False
    value = payload.get(
        "synthetic",
        payload.get("isSynthetic", payload.get("is_synthetic", False)),
    )
    return value is True


def _parse_datetime(value: object, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise DispatchValidationError(
            "An operating constraint contains an invalid timestamp.",
            field_details={field: "must_be_an_offset_datetime"},
        ) from error
    if parsed.tzinfo is None:
        raise DispatchValidationError(
            "An operating constraint contains a naive timestamp.",
            field_details={field: "utc_offset_required"},
        )
    return parsed.astimezone(UTC)


def _constraint_view(item: Any) -> OperatingConstraintView:
    return OperatingConstraintView(
        id=item.id,
        code=item.code,
        name=item.name,
        constraint_type=item.constraint_type,
        is_hard=item.is_hard,
        valid_from=item.valid_from,
        valid_to=item.valid_to,
        configuration=item.configuration,
        is_active=item.is_active,
    )


def _load_view(
    load: Any,
    constraints: list[Any],
    snapshot: FrozenLoadSnapshot | None = None,
) -> FlexibleLoadView:
    views = [
        item if isinstance(item, OperatingConstraintView) else _constraint_view(item)
        for item in constraints
    ]
    return FlexibleLoadView(
        id=load.id,
        company_id=load.company_id,
        site_id=load.site_id,
        semantic_entity_id=load.semantic_entity_id,
        code=snapshot.code if snapshot is not None else load.code,
        name=snapshot.name if snapshot is not None else load.name,
        description=load.description,
        power_kw=snapshot.power_kw if snapshot is not None else load.power_kw,
        duration_minutes=(
            snapshot.duration_minutes if snapshot is not None else load.duration_minutes
        ),
        energy_kwh=snapshot.energy_kwh if snapshot is not None else load.energy_kwh,
        minimum_power_kw=(
            snapshot.minimum_power_kw if snapshot is not None else load.minimum_power_kw
        ),
        maximum_power_kw=(
            snapshot.maximum_power_kw if snapshot is not None else load.maximum_power_kw
        ),
        is_interruptible=(
            snapshot.is_interruptible if snapshot is not None else load.is_interruptible
        ),
        metadata=load.load_metadata,
        is_active=snapshot.is_active if snapshot is not None else load.is_active,
        constraints=views,
        created_at=load.created_at,
        updated_at=load.updated_at,
    )


def _freeze_load(load: Any) -> FrozenLoadSnapshot:
    payload = {
        "id": load.id,
        "code": load.code,
        "name": load.name,
        "power_kw": load.power_kw,
        "duration_minutes": load.duration_minutes,
        "energy_kwh": load.energy_kwh,
        "minimum_power_kw": load.minimum_power_kw,
        "maximum_power_kw": load.maximum_power_kw,
        "is_interruptible": load.is_interruptible,
        "is_active": load.is_active,
    }
    return FrozenLoadSnapshot(**payload, snapshot_hash=_hash(payload))


def _freeze_method(method: Any) -> FrozenMethodSnapshot:
    payload = {
        "id": method.id,
        "key": method.key,
        "version": method.version,
        "code_version": method.code_version,
        "configuration": method.configuration,
        "is_active": method.is_active,
    }
    return FrozenMethodSnapshot(**payload, snapshot_hash=_hash(payload))


def _freeze_policy(policy: Any | None) -> FrozenPolicySnapshot | None:
    if policy is None:
        return None
    payload = {
        "id": policy.id,
        "key": policy.key,
        "version": policy.version,
        "configuration": policy.configuration,
        "is_active": policy.is_active,
    }
    return FrozenPolicySnapshot(**payload, snapshot_hash=_hash(payload))


def _forecast_point_payload(point: GridForecast) -> dict[str, Any]:
    metadata = point.provider_metadata or {}
    return {
        "provider": point.provider,
        "api_version": metadata.get("api_version"),
        "zone": point.zone,
        "forecast_for": point.forecast_for,
        "issued_at": point.issued_at,
        "intensity_gco2e_per_kwh": _canonical_forecast_intensity(
            point.intensity_gco2e_per_kwh
        ),
        "emission_factor_type": point.emission_factor_type,
        "flow_traced": point.flow_traced,
        "is_estimated": point.is_estimated,
        "estimation_method": metadata.get("estimation_method"),
        "temporal_granularity": point.temporal_granularity,
    }


class DispatchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = DispatchRepository(session)

    async def list_loads(
        self,
        *,
        company_id: UUID,
        site_id: UUID | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> FlexibleLoadList:
        records, total = await self.repository.list_loads(
            company_id=company_id,
            site_id=site_id,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        return FlexibleLoadList(
            items=[_load_view(item.load, item.constraints) for item in records],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def sync_forecast(
        self,
        request: ForecastSyncRequest,
        *,
        provider: ElectricityMapsProvider,
    ) -> ForecastSyncResult:
        site = await self.repository.get_site(
            company_id=request.company_id,
            site_id=request.site_id,
        )
        if site is None:
            raise DispatchNotFoundError("The requested site was not found.")
        configured_zone = (
            site.electricity_maps_zone.strip().upper()
            if site.electricity_maps_zone
            else None
        )
        if request.zone is not None and configured_zone is not None and request.zone != configured_zone:
            raise DispatchValidationError(
                "The requested grid zone does not match the site's configured zone.",
                field_details={"zone": "site_context_mismatch"},
            )
        zone = request.zone or configured_zone
        if zone is None:
            raise DispatchValidationError(
                "No Electricity Maps zone is configured for this site.",
                field_details={"zone": "required"},
            )

        expected_synthetic = request.source_mode == "fixture"
        if not request.force_refresh:
            cached_document = await self.repository.get_latest_forecast_document(
                company_id=request.company_id,
                site_id=request.site_id,
                zone=zone,
                synthetic=expected_synthetic,
            )
            if cached_document is not None:
                cached_points = await self.repository.list_forecast_points(
                    company_id=request.company_id,
                    source_document_id=cached_document.id,
                )
                cached_result = self._cached_forecast_result(
                    request=request,
                    zone=zone,
                    document=cached_document,
                    points=cached_points,
                )
                if cached_result is not None:
                    await self.session.commit()
                    return cached_result

        # Provider latency must not hold an open database transaction.
        await self.session.commit()
        try:
            payload = await fetch_normalized_grid_forecast(
                provider,
                zone=zone,
                horizon_hours=request.horizon_hours,
            )
        except DispatchError:
            raise
        except (ElectricityMapsProviderError, OSError, RuntimeError, TimeoutError) as error:
            retryable = bool(getattr(error, "retryable", True))
            code = str(getattr(error, "code", ForecastProviderError.code))
            status_code = 503 if retryable or code == "integration_not_configured" else 502
            if code == "integration_authentication_failed":
                status_code = 401
            raise ForecastProviderError(
                "The grid forecast provider is unavailable.",
                code=code,
                status_code=status_code,
                retryable=retryable,
            ) from error
        if (
            payload.zone != zone
            or payload.horizon_hours != EXPECTED_FORECAST_POINTS
            or len(payload.points) != EXPECTED_FORECAST_POINTS
            or any(
                item.forecast_for.minute != 0
                or item.forecast_for.second != 0
                or item.forecast_for.microsecond != 0
                for item in payload.points
            )
        ):
            raise DispatchValidationError(
                "Dispatch requires exactly 24 points aligned to whole UTC hours.",
                code="dispatch_invalid_normalized_forecast",
                status_code=502,
            )
        if raw_synthetic(payload.source_snapshot) != expected_synthetic:
            raise DispatchValidationError(
                "The forecast source mode does not match its synthetic provenance marker.",
                code="dispatch_forecast_provenance_mismatch",
                status_code=502,
            )
        snapshot = {
            "provider": FORECAST_PROVIDER,
            "api_version": FORECAST_API_VERSION,
            "endpoint": FORECAST_ENDPOINT,
            "site_id": request.site_id,
            "zone": zone,
            "issued_at": payload.issued_at,
            "provider_response_checksum": payload.response_checksum,
            "response": payload.source_snapshot,
        }
        snapshot_bytes = _json_bytes(snapshot)
        snapshot_hash = hashlib.sha256(snapshot_bytes).hexdigest()

        try:
            result = await self._persist_forecast(
                request=request,
                zone=zone,
                payload=payload,
                snapshot=snapshot,
                snapshot_bytes=snapshot_bytes,
                snapshot_hash=snapshot_hash,
            )
            await self.session.commit()
            return result
        except IntegrityError as error:
            await self.session.rollback()
            concurrent_document = await self.repository.get_document_by_checksum(
                company_id=request.company_id,
                checksum=snapshot_hash,
            )
            if concurrent_document is not None:
                concurrent_points = await self.repository.list_forecast_points(
                    company_id=request.company_id,
                    source_document_id=concurrent_document.id,
                )
                concurrent_result = self._cached_forecast_result(
                    request=request,
                    zone=zone,
                    document=concurrent_document,
                    points=concurrent_points,
                )
                if concurrent_result is not None:
                    return concurrent_result
            raise DispatchConflictError(
                "The immutable forecast identity conflicts with an existing snapshot."
            ) from error
        except Exception:
            await self.session.rollback()
            raise

    @staticmethod
    def _cached_forecast_result(
        *,
        request: ForecastSyncRequest,
        zone: str,
        document: SourceDocument,
        points: list[GridForecast],
    ) -> ForecastSyncResult | None:
        ordered = sorted(points, key=lambda item: item.forecast_for)
        if (
            len(ordered) != EXPECTED_FORECAST_POINTS
            or len({item.issued_at for item in ordered}) != 1
            or any(item.zone != zone for item in ordered)
            or any(
                later.forecast_for - earlier.forecast_for != timedelta(hours=1)
                for earlier, later in pairwise(ordered)
            )
        ):
            return None
        forecast_end = ordered[-1].forecast_for + timedelta(hours=1)
        if request.source_mode == "live" and forecast_end <= datetime.now(UTC):
            return None
        return ForecastSyncResult(
            site_id=request.site_id,
            zone=zone,
            provider=ordered[0].provider,
            source_mode=request.source_mode,
            synthetic=request.source_mode == "fixture",
            issued_at=ordered[0].issued_at,
            temporal_granularity="hourly",
            source_document_id=document.id,
            snapshot_hash=document.checksum,
            received_points=len(ordered),
            inserted_points=0,
            existing_points=len(ordered),
            estimated_points=sum(int(item.is_estimated) for item in ordered),
            forecast_start=ordered[0].forecast_for,
            forecast_end=forecast_end,
            points=[
                ForecastPointView(
                    id=item.id,
                    forecast_for=item.forecast_for,
                    intensity_gco2e_per_kwh=item.intensity_gco2e_per_kwh,
                    is_estimated=item.is_estimated,
                    point_hash=item.point_hash,
                    evidence_item_id=item.evidence_item_id,
                )
                for item in ordered
            ],
        )

    async def _persist_forecast(
        self,
        *,
        request: ForecastSyncRequest,
        zone: str,
        payload: NormalizedGridForecast,
        snapshot: dict[str, Any],
        snapshot_bytes: bytes,
        snapshot_hash: str,
    ) -> ForecastSyncResult:
        data_source = await self.repository.get_forecast_data_source(
            company_id=request.company_id,
            site_id=request.site_id,
            synthetic=request.source_mode == "fixture",
        )
        if data_source is None:
            data_source = DataSource(
                company_id=request.company_id,
                site_id=request.site_id,
                name=(
                    f"Electricity Maps dispatch forecast {request.source_mode} - "
                    f"{request.site_id}"
                ),
                source_type="synthetic" if request.source_mode == "fixture" else "api",
                status="ready",
                external_reference="electricity-maps:forecast:v4",
                configuration={},
                is_synthetic=request.source_mode == "fixture",
            )
            self.session.add(data_source)
        data_source.status = "ready"
        data_source.configuration = {
            "provider": FORECAST_PROVIDER,
            "api_version": FORECAST_API_VERSION,
            "endpoint": FORECAST_ENDPOINT,
            "zone": zone,
            "horizon_hours": request.horizon_hours,
            "temporal_granularity": "hourly",
            "emission_factor_type": "lifecycle",
            "flow_traced": True,
        }
        await self.session.flush()

        source_document = await self.repository.get_document_by_checksum(
            company_id=request.company_id,
            checksum=snapshot_hash,
        )
        if source_document is None:
            first = payload.points[0].forecast_for
            source_document = SourceDocument(
                company_id=request.company_id,
                data_source_id=data_source.id,
                filename=f"electricity-maps-forecast-{zone}-{first:%Y%m%dT%H%M%SZ}.json",
                content_type="application/json",
                checksum=snapshot_hash,
                version=1,
                size_bytes=len(snapshot_bytes),
                storage_uri=f"database://electricity-maps/forecast/{snapshot_hash}",
                document_metadata={
                    "synthetic": bool(raw_synthetic(snapshot["response"])),
                    "provider": FORECAST_PROVIDER,
                    "api_version": FORECAST_API_VERSION,
                    "endpoint": FORECAST_ENDPOINT,
                    "zone": zone,
                    "issued_at": payload.issued_at.isoformat(),
                    "retrieved_at": payload.retrieved_at.isoformat(),
                    "provider_response_checksum": payload.response_checksum,
                    "forecast_start": payload.points[0].forecast_for.isoformat(),
                    "forecast_end": (
                        payload.points[-1].forecast_for + timedelta(hours=1)
                    ).isoformat(),
                    "response_snapshot": _canonical(snapshot),
                },
            )
            self.session.add(source_document)
            await self.session.flush()

        inserted = 0
        existing = 0
        persisted: list[GridForecast] = []
        evidence_ids: set[UUID] = set()
        for point in payload.points:
            persisted_intensity = _persisted_forecast_intensity(
                point.intensity_gco2e_per_kwh
            )
            point_payload = {
                "provider": FORECAST_PROVIDER,
                "api_version": FORECAST_API_VERSION,
                "zone": zone,
                "forecast_for": point.forecast_for,
                "issued_at": payload.issued_at,
                "intensity_gco2e_per_kwh": _canonical_forecast_intensity(
                    persisted_intensity
                ),
                "emission_factor_type": point.emission_factor_type,
                "flow_traced": point.flow_traced,
                "is_estimated": point.is_estimated,
                "estimation_method": point.estimation_method,
                "temporal_granularity": "hourly",
            }
            point_hash = _hash(point_payload)
            record = await self.repository.get_forecast_identity(
                company_id=request.company_id,
                site_id=request.site_id,
                zone=zone,
                forecast_for=point.forecast_for,
                issued_at=payload.issued_at,
            )
            if record is not None:
                if record.point_hash != point_hash:
                    raise DispatchConflictError(
                        "A provider forecast identity was reused with different data."
                    )
                if record.source_document_id != source_document.id:
                    raise DispatchConflictError(
                        "The forecast identity already belongs to another immutable source snapshot."
                    )
                existing += 1
                persisted.append(record)
                if record.evidence_item_id is not None:
                    evidence_ids.add(record.evidence_item_id)
                continue

            locator = (
                f"forecast:{payload.issued_at.isoformat()}:{point.forecast_for.isoformat()}"
            )
            evidence = await self.repository.get_evidence(
                company_id=request.company_id,
                source_document_id=source_document.id,
                locator=locator,
            )
            if evidence is None:
                content = _json_bytes(point_payload)
                evidence = EvidenceItem(
                    company_id=request.company_id,
                    source_document_id=source_document.id,
                    evidence_type="api_snapshot",
                    locator=locator,
                    content_text=content.decode("utf-8"),
                    checksum=hashlib.sha256(content).hexdigest(),
                    evidence_metadata={
                        "synthetic": bool(raw_synthetic(snapshot["response"])),
                        "provider": FORECAST_PROVIDER,
                        "api_version": FORECAST_API_VERSION,
                        "endpoint": FORECAST_ENDPOINT,
                        "zone": zone,
                        "issued_at": payload.issued_at.isoformat(),
                        "forecast_for": point.forecast_for.isoformat(),
                        "retrieved_at": payload.retrieved_at.isoformat(),
                    },
                )
                self.session.add(evidence)
                await self.session.flush()
            record = GridForecast(
                company_id=request.company_id,
                site_id=request.site_id,
                source_document_id=source_document.id,
                evidence_item_id=evidence.id,
                provider=FORECAST_PROVIDER,
                zone=zone,
                forecast_for=point.forecast_for,
                issued_at=payload.issued_at,
                temporal_granularity="hourly",
                emission_factor_type=point.emission_factor_type,
                flow_traced=point.flow_traced,
                is_estimated=point.is_estimated,
                intensity_gco2e_per_kwh=persisted_intensity,
                point_hash=point_hash,
                provider_metadata={
                    "synthetic": bool(raw_synthetic(snapshot["response"])),
                    "api_version": FORECAST_API_VERSION,
                    "endpoint": FORECAST_ENDPOINT,
                    "retrieved_at": payload.retrieved_at.isoformat(),
                    "estimation_method": point.estimation_method,
                    "snapshot_hash": snapshot_hash,
                },
            )
            self.session.add(record)
            inserted += 1
            persisted.append(record)
            evidence_ids.add(evidence.id)
        await self.session.flush()

        forecast_event = await self.repository.get_forecast_ledger_event(
            company_id=request.company_id,
            source_document_id=source_document.id,
        )
        if forecast_event is None:
            event_payload = {
                "source_document_id": source_document.id,
                "site_id": request.site_id,
                "zone": zone,
                "issued_at": payload.issued_at,
                "forecast_start": payload.points[0].forecast_for,
                "forecast_end": payload.points[-1].forecast_for + timedelta(hours=1),
                "point_hashes": [item.point_hash for item in persisted],
                "snapshot_hash": snapshot_hash,
            }
            forecast_event = LedgerEvent(
                company_id=request.company_id,
                event_type="dispatch_forecast_synced",
                entity_type="dispatch_forecast_snapshot",
                entity_id=source_document.id,
                payload=_canonical(event_payload),
                payload_hash=_hash(event_payload),
                analysis_signature=None,
                created_by=None,
                agent_run_id=None,
            )
            self.session.add(forecast_event)
            await self.session.flush()
            for evidence_id in sorted(evidence_ids, key=str):
                self.session.add(
                    LedgerEventEvidence(
                        company_id=request.company_id,
                        ledger_event_id=forecast_event.id,
                        evidence_item_id=evidence_id,
                        relevance="Immutable hourly grid forecast input",
                    )
                )
            await self.session.flush()

        persisted.sort(key=lambda item: item.forecast_for)
        return ForecastSyncResult(
            site_id=request.site_id,
            zone=zone,
            provider=FORECAST_PROVIDER,
            source_mode=request.source_mode,
            synthetic=request.source_mode == "fixture",
            issued_at=payload.issued_at,
            temporal_granularity="hourly",
            source_document_id=source_document.id,
            snapshot_hash=snapshot_hash,
            received_points=len(payload.points),
            inserted_points=inserted,
            existing_points=existing,
            estimated_points=sum(int(item.is_estimated) for item in payload.points),
            forecast_start=payload.points[0].forecast_for,
            forecast_end=payload.points[-1].forecast_for + timedelta(hours=1),
            points=[
                ForecastPointView(
                    id=item.id,
                    forecast_for=item.forecast_for,
                    intensity_gco2e_per_kwh=item.intensity_gco2e_per_kwh,
                    is_estimated=item.is_estimated,
                    point_hash=item.point_hash,
                    evidence_item_id=item.evidence_item_id,
                )
                for item in persisted
            ],
        )

    async def create_scenario(
        self,
        request: CreateDispatchScenarioRequest,
    ) -> DispatchScenarioView:
        dependencies = await self.repository.get_scenario_dependencies(
            company_id=request.company_id,
            site_id=request.site_id,
            load_id=request.flexible_load_id,
            method_id=request.method_definition_id,
            policy_id=request.policy_definition_id,
            source_document_id=request.forecast_source_document_id,
            requested_by=request.requested_by,
            window_start=request.window_start,
            window_end=request.window_end,
        )
        if dependencies is None:
            raise DispatchNotFoundError(
                "One or more tenant-scoped Dispatch scenario dependencies were not found."
            )
        load = dependencies.load
        method = dependencies.method
        policy = dependencies.policy
        if not load.is_active:
            raise DispatchValidationError("The selected flexible load is inactive.")
        if load.is_interruptible:
            raise DispatchValidationError(
                "Interruptible load profiles are not supported by the consecutive-window optimizer."
            )
        if load.duration_minutes % 60 != 0:
            raise DispatchValidationError(
                "Hourly Dispatch requires a whole-hour flexible-load duration.",
                field_details={"flexible_load_id": "duration_not_hour_aligned"},
            )
        expected_energy = load.power_kw * Decimal(load.duration_minutes) / Decimal(60)
        if not load.is_interruptible and load.energy_kwh != expected_energy:
            raise DispatchValidationError(
                "The fixed load's energy does not equal power multiplied by duration.",
                field_details={"flexible_load_id": "inconsistent_fixed_load_energy"},
            )
        if not method.is_active or method.method_type != "dispatch_optimization":
            raise DispatchValidationError(
                "The selected method is not an active Dispatch optimization method."
            )
        if policy is not None and (not policy.is_active or policy.policy_type != "dispatch"):
            raise DispatchValidationError("The selected policy is not an active Dispatch policy.")
        if not dependencies.requester.is_active:
            raise DispatchValidationError("The requesting actor is inactive.")

        forecasts = await self.repository.list_forecast_points(
            company_id=request.company_id,
            source_document_id=request.forecast_source_document_id,
        )
        evidence_by_id, forecast_event = await self._validate_forecast_provenance(
            forecasts,
            company_id=request.company_id,
            source_document=dependencies.source_document,
            site_id=request.site_id,
            window_start=request.window_start,
            window_end=request.window_end,
        )
        frozen_constraints = self._freeze_constraints(request, dependencies.constraints)
        frozen_constraints = frozen_constraints.model_copy(
            update={
                "load_snapshot": _freeze_load(load),
                "method_snapshot": _freeze_method(method),
                "policy_snapshot": _freeze_policy(policy),
            }
        )
        if any(
            value.minute != 0 or value.second != 0 or value.microsecond != 0
            for value in (
                frozen_constraints.earliest_start,
                frozen_constraints.latest_finish,
                frozen_constraints.baseline_start,
            )
        ):
            raise DispatchValidationError(
                "Dispatch window boundaries must align to whole UTC hours."
            )
        if (
            frozen_constraints.baseline_start + timedelta(minutes=load.duration_minutes)
            > request.window_end
        ):
            raise DispatchValidationError(
                "The baseline load cannot finish inside the forecast-backed scenario window."
            )
        forecast_snapshot = [
            {
                "id": str(item.id),
                "forecast_for": item.forecast_for.astimezone(UTC).isoformat(),
                "intensity_gco2e_per_kwh": str(item.intensity_gco2e_per_kwh),
                "is_estimated": item.is_estimated,
                "point_hash": item.point_hash,
                "evidence_item_id": (
                    str(item.evidence_item_id) if item.evidence_item_id is not None else None
                ),
                "evidence_checksum": evidence_by_id[item.evidence_item_id].checksum,
                "source_document_id": str(item.source_document_id),
                "source_document_checksum": dependencies.source_document.checksum,
                "forecast_ledger_event_id": str(forecast_event.id),
                "forecast_ledger_event_hash": forecast_event.payload_hash,
                "zone": item.zone,
                "issued_at": item.issued_at.astimezone(UTC).isoformat(),
            }
            for item in forecasts
        ]
        context = {
            "company_id": request.company_id,
            "site_id": request.site_id,
            "flexible_load_id": request.flexible_load_id,
            "requested_by": request.requested_by,
            "window_start": request.window_start,
            "window_end": request.window_end,
            "objective": request.objective,
        }
        if request.agent_run_id is not None:
            context["agent_run_id"] = request.agent_run_id
        context_hash = _hash(context)
        frozen_load = frozen_constraints.load_snapshot
        frozen_method = frozen_constraints.method_snapshot
        if frozen_load is None or frozen_method is None:  # pragma: no cover - construction invariant
            raise RuntimeError("The frozen Dispatch dependency snapshot is incomplete.")
        frozen_policy = frozen_constraints.policy_snapshot
        signature_input = {
            "context_hash": context_hash,
            "load": {
                "power_kw": frozen_load.power_kw,
                "duration_minutes": frozen_load.duration_minutes,
                "energy_kwh": frozen_load.energy_kwh,
            },
            "method": {
                "id": frozen_method.id,
                "key": frozen_method.key,
                "version": frozen_method.version,
                "code_version": frozen_method.code_version,
                "configuration": frozen_method.configuration,
            },
            "policy": (
                {
                    "id": frozen_policy.id,
                    "key": frozen_policy.key,
                    "version": frozen_policy.version,
                    "configuration": frozen_policy.configuration,
                }
                if frozen_policy is not None
                else None
            ),
            "constraints": self._optimization_constraint_payload(frozen_constraints),
            "forecast_point_hashes": [item.point_hash for item in forecasts],
        }
        analysis_signature = _hash(signature_input)
        if not frozen_constraints.approval_idempotency_key:
            frozen_constraints = frozen_constraints.model_copy(
                update={
                    "approval_idempotency_key": (
                        f"dispatch-preview:{request.company_id}:{analysis_signature}"
                    )
                }
            )
        existing = await self.repository.find_scenario_by_signature(
            company_id=request.company_id,
            analysis_signature=analysis_signature,
        )
        if existing is not None:
            record = await self.repository.get_scenario(
                company_id=request.company_id,
                scenario_id=existing.id,
            )
            if record is None:
                raise DispatchConflictError("The existing Dispatch scenario is incomplete.")
            existing_frozen = FrozenDispatchConstraints.model_validate(
                record.scenario.constraint_snapshot
            )
            if (
                request.idempotency_key is not None
                and existing_frozen.approval_idempotency_key != request.idempotency_key
            ) or (
                request.approval_expires_at is not None
                and existing_frozen.approval_expires_at != request.approval_expires_at
            ):
                raise DispatchConflictError(
                    "The scenario analysis already exists with different explicit approval metadata.",
                    code="dispatch_idempotency_conflict",
                )
            return self._scenario_view(record)

        scenario = DispatchScenario(
            company_id=request.company_id,
            site_id=request.site_id,
            flexible_load_id=request.flexible_load_id,
            agent_run_id=request.agent_run_id,
            method_definition_id=request.method_definition_id,
            policy_definition_id=request.policy_definition_id,
            forecast_source_document_id=request.forecast_source_document_id,
            window_start=request.window_start,
            window_end=request.window_end,
            objective=request.objective,
            constraint_snapshot=frozen_constraints.model_dump(mode="json"),
            forecast_snapshot=forecast_snapshot,
            analysis_signature=analysis_signature,
            context_hash=context_hash,
            status="draft",
        )
        self.session.add(scenario)
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            duplicate = await self.repository.find_scenario_by_signature(
                company_id=request.company_id,
                analysis_signature=analysis_signature,
            )
            if duplicate is None:
                raise DispatchConflictError(
                    "The Dispatch scenario conflicts with existing state."
                ) from error
            scenario = duplicate
        record = await self.repository.get_scenario(
            company_id=request.company_id,
            scenario_id=scenario.id,
        )
        if record is None:
            raise DispatchConflictError("The persisted Dispatch scenario could not be read.")
        return self._scenario_view(record)

    @staticmethod
    def _validate_frozen_forecast(
        forecasts: list[GridForecast],
        *,
        site_id: UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> None:
        if len(forecasts) != EXPECTED_FORECAST_POINTS:
            raise DispatchValidationError(
                "The source document does not contain a complete 24-hour forecast.",
                field_details={"forecast_source_document_id": "complete_24_hour_snapshot_required"},
            )
        ordered = sorted(forecasts, key=lambda item: item.forecast_for)
        if any(item.site_id != site_id for item in ordered):
            raise DispatchValidationError("The forecast belongs to a different site.")
        zones = {item.zone for item in ordered}
        issues = {item.issued_at for item in ordered}
        if len(zones) != 1 or len(issues) != 1:
            raise DispatchValidationError("The forecast snapshot mixes provider identities.")
        if any(
            item.temporal_granularity != "hourly"
            or item.emission_factor_type != "lifecycle"
            or not item.flow_traced
            for item in ordered
        ):
            raise DispatchValidationError("The forecast snapshot uses an unsupported method.")
        if any(
            later.forecast_for - earlier.forecast_for != timedelta(hours=1)
            for earlier, later in pairwise(ordered)
        ):
            raise DispatchValidationError(
                "The forecast snapshot has a missing interval; interpolation is prohibited."
            )
        coverage_start = ordered[0].forecast_for
        coverage_end = ordered[-1].forecast_for + timedelta(hours=1)
        if window_start < coverage_start or window_end > coverage_end:
            raise DispatchValidationError(
                "The requested operating window is outside the forecast snapshot."
            )

    async def _validate_forecast_provenance(
        self,
        forecasts: list[GridForecast],
        *,
        company_id: UUID,
        source_document: SourceDocument,
        site_id: UUID,
        window_start: datetime,
        window_end: datetime,
        expected_snapshot: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[UUID, EvidenceItem], LedgerEvent]:
        def fail(message: str) -> None:
            if expected_snapshot is not None:
                raise DispatchConflictError(
                    message,
                    code="dispatch_scenario_stale",
                    field_details={"forecast_source_document_id": "provenance_changed"},
                )
            raise DispatchValidationError(
                message,
                code="dispatch_invalid_forecast_provenance",
                field_details={"forecast_source_document_id": "invalid_provenance"},
            )

        self._validate_frozen_forecast(
            forecasts,
            site_id=site_id,
            window_start=window_start,
            window_end=window_end,
        )
        ordered = sorted(forecasts, key=lambda item: item.forecast_for)
        if any(item.source_document_id != source_document.id for item in ordered):
            fail("The forecast points do not belong to the selected source document.")

        response_snapshot = source_document.document_metadata.get("response_snapshot")
        if (
            not isinstance(response_snapshot, Mapping)
            or hashlib.sha256(_json_bytes(response_snapshot)).hexdigest()
            != source_document.checksum
        ):
            fail("The immutable forecast source checksum no longer matches its snapshot.")

        evidence_ids = {item.evidence_item_id for item in ordered}
        if None in evidence_ids or len(evidence_ids) != EXPECTED_FORECAST_POINTS:
            fail("Every forecast point must have one distinct evidence item.")
        typed_evidence_ids = {item for item in evidence_ids if item is not None}
        evidence_items = await self.repository.list_evidence_items(
            company_id=company_id,
            evidence_item_ids=typed_evidence_ids,
        )
        evidence_by_id = {item.id: item for item in evidence_items}
        if set(evidence_by_id) != typed_evidence_ids:
            fail("One or more forecast evidence items are missing.")

        for point in ordered:
            evidence_id = point.evidence_item_id
            if evidence_id is None:  # pragma: no cover - rejected above
                fail("A forecast point is missing evidence.")
            evidence = evidence_by_id[evidence_id]
            point_payload = _forecast_point_payload(point)
            expected_content = _json_bytes(point_payload)
            expected_hash = hashlib.sha256(expected_content).hexdigest()
            expected_locator = (
                f"forecast:{point.issued_at.isoformat()}:{point.forecast_for.isoformat()}"
            )
            if (
                point.provider != FORECAST_PROVIDER
                or point.provider_metadata.get("api_version") != FORECAST_API_VERSION
                or point.point_hash != expected_hash
                or evidence.source_document_id != source_document.id
                or evidence.locator != expected_locator
                or evidence.content_text.encode("utf-8") != expected_content
                or evidence.checksum != expected_hash
            ):
                fail("A forecast point or its evidence no longer matches its immutable hash.")

        forecast_event = await self.repository.get_forecast_ledger_event(
            company_id=company_id,
            source_document_id=source_document.id,
        )
        expected_hashes = [item.point_hash for item in ordered]
        if (
            forecast_event is None
            or _hash(forecast_event.payload) != forecast_event.payload_hash
            or forecast_event.payload.get("snapshot_hash") != source_document.checksum
            or forecast_event.payload.get("point_hashes") != expected_hashes
        ):
            fail("The forecast snapshot is missing its matching immutable ledger event.")
        linked_evidence_ids = await self.repository.list_ledger_event_evidence_ids(
            company_id=company_id,
            ledger_event_id=forecast_event.id,
        )
        if linked_evidence_ids != typed_evidence_ids:
            fail("The forecast ledger event does not bind the complete evidence set.")

        if expected_snapshot is not None:
            expected_by_id = {
                str(item.get("id")): item
                for item in expected_snapshot
                if isinstance(item, Mapping)
            }
            if len(expected_by_id) != EXPECTED_FORECAST_POINTS:
                fail("The frozen forecast snapshot is incomplete.")
            for point in ordered:
                frozen_point = expected_by_id.get(str(point.id))
                if frozen_point is None:
                    fail("A forecast point changed after scenario creation.")
                evidence = evidence_by_id[point.evidence_item_id]
                if (
                    frozen_point.get("point_hash") != point.point_hash
                    or frozen_point.get("evidence_item_id") != str(point.evidence_item_id)
                    or frozen_point.get("evidence_checksum") != evidence.checksum
                    or frozen_point.get("source_document_id") != str(source_document.id)
                    or frozen_point.get("source_document_checksum")
                    != source_document.checksum
                    or frozen_point.get("forecast_ledger_event_id")
                    != str(forecast_event.id)
                    or frozen_point.get("forecast_ledger_event_hash")
                    != forecast_event.payload_hash
                ):
                    fail("Forecast provenance changed after scenario creation.")
        return evidence_by_id, forecast_event

    @staticmethod
    def _freeze_constraints(
        request: CreateDispatchScenarioRequest,
        constraints: list[Any],
    ) -> FrozenDispatchConstraints:
        earliest = request.window_start
        latest = request.window_end
        baseline = request.baseline_start
        maximum_delay = request.maximum_delay_minutes
        blackouts = list(request.blackout_windows)
        capacity: list[CapacityWindow] = []
        if request.available_capacity_kw is not None:
            capacity.append(
                CapacityWindow(available_capacity_kw=request.available_capacity_kw)
            )

        for item in constraints:
            if not item.is_hard:
                continue
            config = item.configuration
            if item.constraint_type == "availability":
                if "earliest_start" in config:
                    earliest = max(
                        earliest,
                        _parse_datetime(config["earliest_start"], field="earliest_start"),
                    )
                elif item.valid_from is not None:
                    earliest = max(earliest, item.valid_from.astimezone(UTC))
                if "latest_finish" in config:
                    latest = min(
                        latest,
                        _parse_datetime(config["latest_finish"], field="latest_finish"),
                    )
                elif item.valid_to is not None:
                    latest = min(latest, item.valid_to.astimezone(UTC))
            elif item.constraint_type == "deadline":
                if "maximum_delay_minutes" in config:
                    raw_delay = config["maximum_delay_minutes"]
                    if isinstance(raw_delay, bool) or not isinstance(raw_delay, int):
                        raise DispatchValidationError(
                            "A deadline constraint requires an exact integer maximum delay."
                        )
                    configured_delay = raw_delay
                    if configured_delay < 0:
                        raise DispatchValidationError("Maximum delay cannot be negative.")
                    maximum_delay = min(maximum_delay, configured_delay)
                if "latest_finish" in config:
                    latest = min(
                        latest,
                        _parse_datetime(config["latest_finish"], field="latest_finish"),
                    )
                if "baseline_start" in config:
                    configured_baseline = _parse_datetime(
                        config["baseline_start"], field="baseline_start"
                    )
                    if configured_baseline != baseline:
                        raise DispatchValidationError(
                            "The requested baseline does not match the hard load constraint."
                        )
            elif item.constraint_type == "blackout":
                start_raw = config.get("start", item.valid_from)
                end_raw = config.get("end", item.valid_to)
                if start_raw is None or end_raw is None:
                    raise DispatchValidationError(
                        "A hard blackout constraint must define start and end."
                    )
                try:
                    blackout = BlackoutWindow(
                        start=(
                            start_raw.astimezone(UTC)
                            if isinstance(start_raw, datetime)
                            else _parse_datetime(start_raw, field="blackout.start")
                        ),
                        end=(
                            end_raw.astimezone(UTC)
                            if isinstance(end_raw, datetime)
                            else _parse_datetime(end_raw, field="blackout.end")
                        ),
                    )
                except ValidationError as error:
                    raise DispatchValidationError(
                        "A hard blackout constraint contains an invalid window."
                    ) from error
                blackouts.append(blackout)
            elif item.constraint_type == "power":
                value = config.get("available_capacity_kw", config.get("capacity_kw"))
                if value is None:
                    raise DispatchValidationError(
                        "A hard power constraint must define available_capacity_kw."
                    )
                try:
                    available = Decimal(str(value))
                except Exception as error:
                    raise DispatchValidationError(
                        "A hard power constraint contains invalid capacity."
                    ) from error
                start_raw = config.get("start", item.valid_from)
                end_raw = config.get("end", item.valid_to)
                try:
                    capacity_window = CapacityWindow(
                        available_capacity_kw=available,
                        start=(
                            _parse_datetime(start_raw, field="power.start")
                            if start_raw is not None
                            else None
                        ),
                        end=(
                            _parse_datetime(end_raw, field="power.end")
                            if end_raw is not None
                            else None
                        ),
                    )
                except ValidationError as error:
                    raise DispatchValidationError(
                        "A hard power constraint contains an invalid window."
                    ) from error
                capacity.append(capacity_window)
            elif item.constraint_type in {"cost", "dependency"}:
                raise DispatchValidationError(
                    "This hard operating constraint is not supported by the deterministic optimizer.",
                    code="dispatch_unsupported_hard_constraint",
                    field_details={"constraint": item.code},
                )

        if earliest >= latest:
            raise DispatchValidationError("Hard constraints leave no allowed operating interval.")
        expiry = request.approval_expires_at or datetime.now(UTC) + APPROVAL_TTL
        idempotency = request.idempotency_key or ""
        return FrozenDispatchConstraints(
            earliest_start=earliest,
            latest_finish=latest,
            baseline_start=baseline,
            maximum_delay_minutes=maximum_delay,
            blackouts=sorted(blackouts, key=lambda item: (item.start, item.end)),
            capacity_windows=sorted(
                capacity,
                key=lambda item: (
                    item.start or datetime.min.replace(tzinfo=UTC),
                    item.end or datetime.max.replace(tzinfo=UTC),
                    item.available_capacity_kw,
                ),
            ),
            source_constraints=[_constraint_view(item) for item in constraints],
            requested_by=request.requested_by,
            approval_expires_at=expiry,
            approval_expiry_is_default=request.approval_expires_at is None,
            approval_idempotency_key=idempotency,
        )

    @staticmethod
    def _optimization_constraint_payload(
        constraints: FrozenDispatchConstraints,
    ) -> dict[str, Any]:
        return {
            "earliest_start": constraints.earliest_start,
            "latest_finish": constraints.latest_finish,
            "baseline_start": constraints.baseline_start,
            "maximum_delay_minutes": constraints.maximum_delay_minutes,
            "blackouts": [
                {"start": item.start, "end": item.end} for item in constraints.blackouts
            ],
            "capacity_windows": [
                {
                    "available_capacity_kw": item.available_capacity_kw,
                    "start": item.start,
                    "end": item.end,
                }
                for item in constraints.capacity_windows
            ],
            "source_constraint_ids": sorted(
                (item.id for item in constraints.source_constraints),
                key=str,
            ),
        }

    @staticmethod
    def _scenario_view(record: Any) -> DispatchScenarioView:
        scenario = record.scenario
        frozen = FrozenDispatchConstraints.model_validate(scenario.constraint_snapshot)
        frozen_method = frozen.method_snapshot
        if frozen_method is None:
            raise DispatchConflictError("The scenario is missing its frozen method snapshot.")
        return DispatchScenarioView(
            id=scenario.id,
            company_id=scenario.company_id,
            site_id=scenario.site_id,
            flexible_load=_load_view(
                record.load,
                frozen.source_constraints,
                frozen.load_snapshot,
            ),
            agent_run_id=scenario.agent_run_id,
            method=DispatchMethodView(
                id=frozen_method.id,
                key=frozen_method.key,
                version=frozen_method.version,
                code_version=frozen_method.code_version,
            ),
            policy_definition_id=scenario.policy_definition_id,
            forecast_source_document_id=scenario.forecast_source_document_id,
            window_start=scenario.window_start,
            window_end=scenario.window_end,
            objective=scenario.objective,
            constraints=frozen,
            forecast_snapshot=scenario.forecast_snapshot,
            analysis_signature=scenario.analysis_signature,
            context_hash=scenario.context_hash,
            status=scenario.status,
            created_at=scenario.created_at,
            updated_at=scenario.updated_at,
        )

    @staticmethod
    def _validate_dependency_snapshots(
        record: Any,
        frozen: FrozenDispatchConstraints,
    ) -> tuple[FrozenLoadSnapshot, FrozenMethodSnapshot, FrozenPolicySnapshot | None]:
        load_snapshot = frozen.load_snapshot
        method_snapshot = frozen.method_snapshot
        policy_snapshot = frozen.policy_snapshot
        if load_snapshot is None or method_snapshot is None:
            raise DispatchConflictError(
                "The Dispatch scenario is missing frozen dependency snapshots.",
                code="dispatch_scenario_stale",
            )
        current_load = _freeze_load(record.load)
        current_method = _freeze_method(record.method)
        current_policy = _freeze_policy(record.policy)
        if (
            current_load.snapshot_hash != load_snapshot.snapshot_hash
            or current_method.snapshot_hash != method_snapshot.snapshot_hash
            or (current_policy is None) != (policy_snapshot is None)
            or (
                current_policy is not None
                and policy_snapshot is not None
                and current_policy.snapshot_hash != policy_snapshot.snapshot_hash
            )
        ):
            raise DispatchConflictError(
                "A load, method, or policy changed after the Dispatch scenario was frozen.",
                code="dispatch_scenario_stale",
                field_details={"scenario_id": "dependency_snapshot_changed"},
            )
        return load_snapshot, method_snapshot, policy_snapshot

    async def optimize_scenario(
        self,
        scenario_id: UUID,
        request: OptimizeScenarioRequest,
    ) -> DispatchOptimizationResult:
        record = await self.repository.get_scenario(
            company_id=request.company_id,
            scenario_id=scenario_id,
            for_update=True,
        )
        if record is None:
            raise DispatchNotFoundError("The Dispatch scenario was not found.")
        scenario = record.scenario
        if scenario.status in {"invalidated", "closed"}:
            raise DispatchConflictError("The Dispatch scenario is no longer optimizable.")
        frozen = FrozenDispatchConstraints.model_validate(scenario.constraint_snapshot)
        frozen_load, _, _ = self._validate_dependency_snapshots(record, frozen)
        source_document = await self.repository.get_source_document(
            company_id=request.company_id,
            source_document_id=scenario.forecast_source_document_id,
        )
        if source_document is None:
            raise DispatchConflictError(
                "The frozen Dispatch forecast source document is missing.",
                code="dispatch_scenario_stale",
            )
        current_forecasts = await self.repository.list_forecast_points(
            company_id=request.company_id,
            source_document_id=scenario.forecast_source_document_id,
        )
        await self._validate_forecast_provenance(
            current_forecasts,
            company_id=request.company_id,
            source_document=source_document,
            site_id=scenario.site_id,
            window_start=scenario.window_start,
            window_end=scenario.window_end,
            expected_snapshot=scenario.forecast_snapshot,
        )
        existing = await self.repository.get_recommendation_for_scenario(
            company_id=request.company_id,
            scenario_id=scenario_id,
        )
        if existing is not None:
            recommendation = self._recommendation_view(existing)
            return DispatchOptimizationResult(
                scenario_id=scenario_id,
                terminal_state="approval_required",
                evaluated_windows=int(
                    existing.recommendation.impact_snapshot.get("evaluated_windows", 0)
                ),
                feasible_windows=int(
                    existing.recommendation.impact_snapshot.get("feasible_windows", 0)
                ),
                rejected_windows=[
                    RejectedWindowView.model_validate(item)
                    for item in existing.recommendation.impact_snapshot.get(
                        "rejected_windows", []
                    )
                ],
                recommendation=recommendation,
            )
        if scenario.status == "no_feasible_window":
            return await self._read_no_feasible_result(scenario)

        forecast_values = tuple(
            ForecastValue(
                forecast_for=datetime.fromisoformat(str(item["forecast_for"])),
                intensity_gco2e_per_kwh=Decimal(
                    str(item["intensity_gco2e_per_kwh"])
                ),
            )
            for item in scenario.forecast_snapshot
        )
        try:
            optimized = optimize_dispatch(
                DispatchInputs(
                    power_kw=frozen_load.power_kw,
                    energy_kwh=frozen_load.energy_kwh,
                    duration_minutes=frozen_load.duration_minutes,
                    earliest_start=frozen.earliest_start,
                    latest_finish=frozen.latest_finish,
                    baseline_start=frozen.baseline_start,
                    maximum_delay_minutes=frozen.maximum_delay_minutes,
                    blackouts=tuple(
                        TimeWindow(item.start, item.end) for item in frozen.blackouts
                    ),
                    capacity_windows=tuple(
                        OptimizationCapacityWindow(
                            available_capacity_kw=item.available_capacity_kw,
                            start=item.start,
                            end=item.end,
                        )
                        for item in frozen.capacity_windows
                    ),
                ),
                forecast_values,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DispatchValidationError(
                "The frozen Dispatch inputs are incomplete or invalid.",
                field_details={"scenario_id": "frozen_input_validation_failed"},
            ) from error

        rejected_views = [
            RejectedWindowView(
                start=item.start,
                end=item.end,
                reasons=list(item.reasons),
            )
            for item in optimized.rejected_windows
        ]
        if optimized.recommended is None:
            return await self._persist_no_feasible_result(
                record=record,
                frozen=frozen,
                baseline=optimized.baseline,
                rejected_views=rejected_views,
            )

        recommendation = await self._persist_recommendation(
            record=record,
            frozen=frozen,
            optimized=optimized,
            rejected_views=rejected_views,
        )
        return DispatchOptimizationResult(
            scenario_id=scenario.id,
            terminal_state="approval_required",
            evaluated_windows=len(optimized.feasible_windows) + len(optimized.rejected_windows),
            feasible_windows=len(optimized.feasible_windows),
            rejected_windows=rejected_views,
            recommendation=recommendation,
        )

    async def _persist_no_feasible_result(
        self,
        *,
        record: Any,
        frozen: FrozenDispatchConstraints,
        baseline: CandidateWindow | None,
        rejected_views: list[RejectedWindowView],
    ) -> DispatchOptimizationResult:
        scenario = record.scenario
        method_snapshot = frozen.method_snapshot
        if method_snapshot is None or baseline is None:  # pragma: no cover - caller invariant
            raise RuntimeError(
                "The infeasible Dispatch result requires frozen method and baseline snapshots."
            )
        payload = {
            "scenario_id": scenario.id,
            "terminal_state": "no_feasible_option",
            "analysis_signature": scenario.analysis_signature,
            "context_hash": scenario.context_hash,
            "method_id": method_snapshot.id,
            "method_version": method_snapshot.version,
            "method_code_version": method_snapshot.code_version,
            "baseline": {
                "start": baseline.start,
                "end": baseline.end,
                "emissions_kgco2e": baseline.emissions_kgco2e,
            },
            "forecast_source_document_id": scenario.forecast_source_document_id,
            "forecast_source_document_checksum": scenario.forecast_snapshot[0][
                "source_document_checksum"
            ],
            "forecast_point_hashes": [
                item["point_hash"] for item in scenario.forecast_snapshot
            ],
            "forecast_evidence_ids": [
                item["evidence_item_id"] for item in scenario.forecast_snapshot
            ],
            "load_snapshot": (
                frozen.load_snapshot.model_dump(mode="python")
                if frozen.load_snapshot is not None
                else None
            ),
            "constraint_snapshot": frozen.model_dump(mode="python"),
            "evaluated_windows": len(rejected_views),
            "feasible_windows": 0,
            "rejected_windows": [item.model_dump(mode="python") for item in rejected_views],
            "advisory_only": True,
            "actuation_authorized": False,
        }
        event = LedgerEvent(
            company_id=scenario.company_id,
            event_type="dispatch_no_feasible_window",
            entity_type="dispatch_scenario",
            entity_id=scenario.id,
            payload=_canonical(payload),
            payload_hash=_hash(payload),
            analysis_signature=scenario.analysis_signature,
            created_by=frozen.requested_by,
            agent_run_id=scenario.agent_run_id,
        )
        self.session.add(event)
        try:
            await self.session.flush()
            evidence_ids = {
                UUID(str(item["evidence_item_id"]))
                for item in scenario.forecast_snapshot
                if item.get("evidence_item_id") is not None
            }
            for evidence_id in sorted(evidence_ids, key=str):
                self.session.add(
                    LedgerEventEvidence(
                        company_id=scenario.company_id,
                        ledger_event_id=event.id,
                        evidence_item_id=evidence_id,
                        relevance="Forecast input to infeasible Dispatch result",
                    )
                )
            forecast_event = await self.repository.get_forecast_ledger_event(
                company_id=scenario.company_id,
                source_document_id=scenario.forecast_source_document_id,
            )
            if forecast_event is None:
                raise DispatchConflictError(
                    "The infeasible Dispatch result is missing forecast lineage."
                )
            self.session.add(
                LineageEdge(
                    company_id=scenario.company_id,
                    parent_event_id=forecast_event.id,
                    child_event_id=event.id,
                    relationship_type="forecast_input_to_dispatch_infeasibility",
                    edge_metadata={
                        "source_document_id": str(scenario.forecast_source_document_id),
                        "analysis_signature": scenario.analysis_signature,
                    },
                )
            )
            scenario.status = "no_feasible_window"
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            existing = await self.repository.get_no_feasible_event(
                company_id=scenario.company_id,
                scenario_id=scenario.id,
            )
            if existing is None:
                raise DispatchConflictError(
                    "The infeasible Dispatch result conflicts with existing state."
                ) from error
        except Exception:
            await self.session.rollback()
            raise
        return await self._read_no_feasible_result(scenario)

    async def _read_no_feasible_result(
        self,
        scenario: DispatchScenario,
    ) -> DispatchOptimizationResult:
        event = await self.repository.get_no_feasible_event(
            company_id=scenario.company_id,
            scenario_id=scenario.id,
        )
        if event is None:
            raise DispatchConflictError(
                "The no-feasible Dispatch scenario is missing its replay event."
            )
        try:
            rejected = [
                RejectedWindowView.model_validate(item)
                for item in event.payload["rejected_windows"]
            ]
            evaluated = int(event.payload["evaluated_windows"])
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise DispatchConflictError(
                "The no-feasible Dispatch replay event is invalid."
            ) from error
        return DispatchOptimizationResult(
            scenario_id=scenario.id,
            terminal_state="no_feasible_option",
            evaluated_windows=evaluated,
            feasible_windows=0,
            rejected_windows=rejected,
            recommendation=None,
        )

    @staticmethod
    def _resolve_approval_expiry(
        frozen: FrozenDispatchConstraints,
        *,
        now: datetime | None = None,
    ) -> datetime:
        checked_at = now or datetime.now(UTC)
        if frozen.approval_expires_at > checked_at:
            return frozen.approval_expires_at
        if not frozen.approval_expiry_is_default:
            raise DispatchConflictError(
                "The explicit Dispatch approval preview expiry has passed.",
                code="dispatch_approval_expired",
            )
        return checked_at + APPROVAL_TTL

    async def _persist_recommendation(
        self,
        *,
        record: Any,
        frozen: FrozenDispatchConstraints,
        optimized: Any,
        rejected_views: list[RejectedWindowView],
    ) -> DispatchRecommendationView:
        scenario = record.scenario
        selected = optimized.recommended
        load_snapshot = frozen.load_snapshot
        method_snapshot = frozen.method_snapshot
        policy_snapshot = frozen.policy_snapshot
        if (
            selected is None
            or optimized.baseline is None
            or optimized.avoided_kgco2e is None
            or optimized.reduction_pct is None
            or load_snapshot is None
            or method_snapshot is None
        ):
            raise RuntimeError("A recommendation requires a complete optimization result.")
        approval_expires_at = self._resolve_approval_expiry(frozen)

        # The analysis signature is the hash of the complete frozen optimizer
        # input assembled during scenario creation.
        input_hash = scenario.analysis_signature
        optimization_output = {
            "baseline": {
                "start": optimized.baseline.start,
                "end": optimized.baseline.end,
                "emissions_kgco2e": optimized.baseline.emissions_kgco2e,
            },
            "recommended": {
                "start": selected.start,
                "end": selected.end,
                "emissions_kgco2e": selected.emissions_kgco2e,
            },
            "avoided_kgco2e": optimized.avoided_kgco2e,
            "reduction_pct": optimized.reduction_pct,
            "feasible_windows": len(optimized.feasible_windows),
            "rejected_windows": len(optimized.rejected_windows),
            "actuation_authorized": False,
        }
        optimization_output_hash = _hash(optimization_output)
        preview_payload = {
            "target_type": "dispatch_recommendation",
            "scenario_id": scenario.id,
            "recommended_start": selected.start,
            "recommended_end": selected.end,
            "baseline_start": optimized.baseline.start,
            "baseline_end": optimized.baseline.end,
            "expected_emissions_kgco2e": selected.emissions_kgco2e,
            "baseline_emissions_kgco2e": optimized.baseline.emissions_kgco2e,
            "avoided_kgco2e": optimized.avoided_kgco2e,
            "reduction_pct": optimized.reduction_pct,
            "analysis_signature": scenario.analysis_signature,
            "context_hash": scenario.context_hash,
            "load_snapshot": load_snapshot.model_dump(mode="python"),
            "method_snapshot": method_snapshot.model_dump(mode="python"),
            "policy_snapshot": (
                policy_snapshot.model_dump(mode="python")
                if policy_snapshot is not None
                else None
            ),
            "constraint_snapshot": self._optimization_constraint_payload(frozen),
            "frozen_constraints": frozen.model_dump(mode="python"),
            "forecast_source_document_id": scenario.forecast_source_document_id,
            "forecast_source_document_checksum": scenario.forecast_snapshot[0][
                "source_document_checksum"
            ],
            "forecast_points": [
                {
                    "id": item["id"],
                    "forecast_for": item["forecast_for"],
                    "intensity_gco2e_per_kwh": item[
                        "intensity_gco2e_per_kwh"
                    ],
                    "point_hash": item["point_hash"],
                    "evidence_item_id": item["evidence_item_id"],
                    "evidence_checksum": item["evidence_checksum"],
                    "source_document_id": item["source_document_id"],
                    "source_document_checksum": item[
                        "source_document_checksum"
                    ],
                    "forecast_ledger_event_id": item[
                        "forecast_ledger_event_id"
                    ],
                    "forecast_ledger_event_hash": item[
                        "forecast_ledger_event_hash"
                    ],
                }
                for item in scenario.forecast_snapshot
            ],
            "approval_expires_at": approval_expires_at,
            "method_id": method_snapshot.id,
            "method_version": method_snapshot.version,
            "method_code_version": method_snapshot.code_version,
            "input_hash": input_hash,
            "output_hash": optimization_output_hash,
            "advisory_only": True,
            "actuation_authorized": False,
        }
        provisional_payload_hash = _hash(
            {
                "target_type": "dispatch_recommendation",
                "scenario_id": scenario.id,
                "analysis_signature": scenario.analysis_signature,
                "state": "pending_identifier",
            }
        )
        impact_snapshot = {
            "input_hash": input_hash,
            "output_hash": optimization_output_hash,
            "method_id": str(method_snapshot.id),
            "method_key": method_snapshot.key,
            "method_version": method_snapshot.version,
            "method_code_version": method_snapshot.code_version,
            "evaluated_windows": len(optimized.feasible_windows)
            + len(optimized.rejected_windows),
            "feasible_windows": len(optimized.feasible_windows),
            "rejected_windows": [item.model_dump(mode="json") for item in rejected_views],
            "forecast_source_document_id": str(scenario.forecast_source_document_id),
            "forecast_source_document_checksum": scenario.forecast_snapshot[0][
                "source_document_checksum"
            ],
            "forecast_point_hashes": [
                item["point_hash"] for item in scenario.forecast_snapshot
            ],
            "forecast_evidence_ids": [
                item["evidence_item_id"] for item in scenario.forecast_snapshot
            ],
            "advisory_only": True,
            "actuation_authorized": False,
        }
        recommendation = DispatchRecommendation(
            company_id=scenario.company_id,
            dispatch_scenario_id=scenario.id,
            ledger_event_id=None,
            recommended_start=selected.start,
            recommended_end=selected.end,
            baseline_start=optimized.baseline.start,
            baseline_end=optimized.baseline.end,
            expected_emissions_kgco2e=selected.emissions_kgco2e,
            baseline_emissions_kgco2e=optimized.baseline.emissions_kgco2e,
            avoided_kgco2e=optimized.avoided_kgco2e,
            reduction_pct=optimized.reduction_pct,
            expected_cost=None,
            currency=None,
            rationale_template=RATIONALE,
            impact_snapshot=impact_snapshot,
            analysis_signature=scenario.analysis_signature,
            payload_hash=provisional_payload_hash,
            status="pending_approval",
            actuation_authorized=False,
        )
        self.session.add(recommendation)
        try:
            await self.session.flush()
            preview_payload["target_id"] = recommendation.id
            payload_hash = _hash(preview_payload)
            recommendation.payload_hash = payload_hash
            ledger_event = LedgerEvent(
                company_id=scenario.company_id,
                event_type="dispatch_recommendation_created",
                entity_type="dispatch_recommendation",
                entity_id=recommendation.id,
                payload=_canonical(preview_payload),
                payload_hash=payload_hash,
                analysis_signature=scenario.analysis_signature,
                created_by=frozen.requested_by,
                agent_run_id=scenario.agent_run_id,
            )
            self.session.add(ledger_event)
            await self.session.flush()
            recommendation.ledger_event_id = ledger_event.id

            evidence_ids = {
                UUID(str(item["evidence_item_id"]))
                for item in scenario.forecast_snapshot
                if item.get("evidence_item_id") is not None
            }
            for evidence_id in sorted(evidence_ids, key=str):
                self.session.add(
                    LedgerEventEvidence(
                        company_id=scenario.company_id,
                        ledger_event_id=ledger_event.id,
                        evidence_item_id=evidence_id,
                        relevance="Forecast input to deterministic Dispatch optimization",
                    )
                )
            forecast_event = await self.repository.get_forecast_ledger_event(
                company_id=scenario.company_id,
                source_document_id=scenario.forecast_source_document_id,
            )
            if forecast_event is None:
                raise DispatchConflictError(
                    "The Dispatch recommendation is missing forecast lineage."
                )
            self.session.add(
                LineageEdge(
                    company_id=scenario.company_id,
                    parent_event_id=forecast_event.id,
                    child_event_id=ledger_event.id,
                    relationship_type="forecast_input_to_dispatch_recommendation",
                    edge_metadata={
                        "source_document_id": str(scenario.forecast_source_document_id),
                        "input_hash": input_hash,
                    },
                )
            )

            if scenario.agent_run_id is not None:
                self._add_fact_bindings(
                    scenario=scenario,
                    recommendation=recommendation,
                    ledger_event=ledger_event,
                    values={
                        "fact_expected_emissions": (
                            selected.emissions_kgco2e,
                            "kgCO2e",
                        ),
                        "fact_baseline_emissions": (
                            optimized.baseline.emissions_kgco2e,
                            "kgCO2e",
                        ),
                        "fact_avoided_emissions": (
                            optimized.avoided_kgco2e,
                            "kgCO2e",
                        ),
                        "fact_reduction_pct": (optimized.reduction_pct, "%"),
                    },
                )

            approval = Approval(
                company_id=scenario.company_id,
                target_type="dispatch_recommendation",
                target_id=recommendation.id,
                recommendation_id=None,
                requested_by=frozen.requested_by,
                decided_by=None,
                ledger_event_id=None,
                policy_definition_id=scenario.policy_definition_id,
                status="pending",
                preview_payload=_canonical(preview_payload),
                preview_hash=payload_hash,
                analysis_signature=scenario.analysis_signature,
                context_hash=scenario.context_hash,
                idempotency_key=frozen.approval_idempotency_key,
                expires_at=approval_expires_at,
                decided_at=None,
                decision_note=None,
            )
            self.session.add(approval)
            scenario.status = "recommended"
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            existing = await self.repository.get_recommendation_for_scenario(
                company_id=scenario.company_id,
                scenario_id=scenario.id,
            )
            if existing is not None:
                return self._recommendation_view(existing)
            raise DispatchConflictError(
                "The Dispatch recommendation conflicts with existing state."
            ) from error
        except Exception:
            await self.session.rollback()
            raise
        persisted = await self.repository.get_recommendation_for_scenario(
            company_id=scenario.company_id,
            scenario_id=scenario.id,
        )
        if persisted is None:
            raise DispatchConflictError("The persisted recommendation could not be read.")
        return self._recommendation_view(persisted)

    def _add_fact_bindings(
        self,
        *,
        scenario: DispatchScenario,
        recommendation: DispatchRecommendation,
        ledger_event: LedgerEvent,
        values: dict[str, tuple[Decimal, str]],
    ) -> None:
        if scenario.agent_run_id is None:
            return
        for placeholder, (value, unit) in values.items():
            value_snapshot = {"value": str(value), "unit": unit}
            binding_payload = {
                "artifact_type": "dispatch_recommendation",
                "artifact_id": recommendation.id,
                "placeholder": placeholder,
                "value": value_snapshot,
                "ledger_event_id": ledger_event.id,
                "context_hash": scenario.context_hash,
            }
            self.session.add(
                FactBinding(
                    company_id=scenario.company_id,
                    artifact_type="dispatch_recommendation",
                    artifact_id=recommendation.id,
                    recommendation_id=None,
                    agent_run_id=scenario.agent_run_id,
                    ledger_event_id=ledger_event.id,
                    evidence_item_id=None,
                    placeholder=placeholder,
                    value_snapshot=value_snapshot,
                    display_value=f"{value} {unit}",
                    unit=unit,
                    context_hash=scenario.context_hash,
                    binding_hash=_hash(binding_payload),
                )
            )

    @staticmethod
    def _recommendation_view(record: Any) -> DispatchRecommendationView:
        recommendation = record.recommendation
        approval = record.approval
        if recommendation.ledger_event_id is None or approval is None:
            raise DispatchConflictError(
                "The Dispatch recommendation is missing its ledger or approval binding."
            )
        return DispatchRecommendationView(
            id=recommendation.id,
            company_id=recommendation.company_id,
            scenario_id=recommendation.dispatch_scenario_id,
            status=recommendation.status,
            recommended_start=recommendation.recommended_start,
            recommended_end=recommendation.recommended_end,
            baseline_start=recommendation.baseline_start,
            baseline_end=recommendation.baseline_end,
            expected_emissions_kgco2e=recommendation.expected_emissions_kgco2e,
            baseline_emissions_kgco2e=recommendation.baseline_emissions_kgco2e,
            avoided_kgco2e=recommendation.avoided_kgco2e,
            reduction_pct=recommendation.reduction_pct,
            rationale=recommendation.rationale_template,
            impact_snapshot=recommendation.impact_snapshot,
            analysis_signature=recommendation.analysis_signature,
            payload_hash=recommendation.payload_hash,
            ledger_event_id=recommendation.ledger_event_id,
            evidence_item_ids=sorted(
                {item.evidence_item_id for item in record.evidence_links},
                key=str,
            ),
            approval=ApprovalPreviewView(
                id=approval.id,
                target_type="dispatch_recommendation",
                target_id=recommendation.id,
                status=approval.status,
                preview_payload=approval.preview_payload,
                preview_hash=approval.preview_hash,
                analysis_signature=approval.analysis_signature,
                context_hash=approval.context_hash,
                idempotency_key=approval.idempotency_key,
                expires_at=approval.expires_at,
            ),
            actuation_authorized=False,
            invalidated_at=recommendation.invalidated_at,
            created_at=recommendation.created_at,
        )

    async def get_recommendation(
        self,
        *,
        company_id: UUID,
        scenario_id: UUID,
    ) -> DispatchRecommendationResult:
        scenario = await self.repository.get_scenario(
            company_id=company_id,
            scenario_id=scenario_id,
        )
        if scenario is None:
            raise DispatchNotFoundError("The Dispatch scenario was not found.")
        recommendation = await self.repository.get_recommendation_for_scenario(
            company_id=company_id,
            scenario_id=scenario_id,
        )
        if recommendation is None:
            if scenario.scenario.status == "no_feasible_window":
                return DispatchRecommendationResult(
                    scenario_id=scenario_id,
                    terminal_state="no_feasible_option",
                    recommendation=None,
                )
            raise DispatchNotFoundError("No Dispatch recommendation exists for this scenario.")
        return DispatchRecommendationResult(
            scenario_id=scenario_id,
            terminal_state="approval_required",
            recommendation=self._recommendation_view(recommendation),
        )
