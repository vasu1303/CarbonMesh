from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import GridIntensityPoint
from app.db.models.core import DataSource, EvidenceItem, Site, SourceDocument
from app.db.models.semantic import MetricDefinition
from app.modules.integrations import repository
from app.modules.integrations.electricity_maps import (
    ElectricityMapsProvider,
    ElectricityMapsProviderError,
)
from app.modules.integrations.schemas import (
    ElectricityMapsRangePayload,
    ElectricityMapsTestResult,
    ElectricityMapsZoneAccess,
    GridIntensityProvenance,
    GridIntensitySyncRequest,
    GridIntensitySyncResult,
    GridZoneResolution,
    LatestGridIntensityResult,
    SnapshotEnvelope,
)

MAX_HOURLY_RANGE = timedelta(hours=240)
MAX_HOURLY_POINTS = 240
KG_PER_GRAM = Decimal("0.001")
KG_INTENSITY_QUANTUM = Decimal("0.000000000001")


def _trim_decimal_scale(value: Decimal) -> Decimal:
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return normalized.quantize(Decimal(1))
    return normalized


class IntegrationServiceError(RuntimeError):
    code = "integration_error"
    status_code = 400
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        field_details: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code if code is not None else type(self).code
        self.status_code = status_code if status_code is not None else type(self).status_code
        self.retryable = retryable if retryable is not None else type(self).retryable
        self.field_details = field_details or {}


class SiteNotFoundError(IntegrationServiceError):
    code = "site_not_found"
    status_code = 404


class GridIntensityNotFoundError(IntegrationServiceError):
    code = "grid_intensity_not_found"
    status_code = 404


class ZoneResolutionError(IntegrationServiceError):
    code = "grid_zone_unresolved"
    status_code = 422


class InvalidProviderResponseError(IntegrationServiceError):
    code = "integration_invalid_response"
    status_code = 502


def _provider_error(error: ElectricityMapsProviderError) -> IntegrationServiceError:
    status_code = 503 if error.retryable else 502
    if error.code in {"integration_not_configured", "integration_live_disabled"}:
        status_code = 503
    if error.code == "integration_authentication_failed":
        status_code = 401
    return IntegrationServiceError(
        str(error),
        code=error.code,
        status_code=status_code,
        retryable=error.retryable,
    )


def _zone_access_items(payload: Any) -> list[ElectricityMapsZoneAccess]:
    if not isinstance(payload, dict):
        raise InvalidProviderResponseError("Electricity Maps returned invalid zone metadata.")
    zones: list[ElectricityMapsZoneAccess] = []
    for key, raw_value in payload.items():
        if not isinstance(key, str) or not isinstance(raw_value, dict):
            continue
        raw_access = raw_value.get("access", [])
        access = [str(item) for item in raw_access] if isinstance(raw_access, list) else []
        if not access:
            continue
        zones.append(
            ElectricityMapsZoneAccess(
                zone=str(raw_value.get("zoneKey") or key).upper(),
                zone_name=str(
                    raw_value.get("displayName")
                    or raw_value.get("zoneName")
                    or raw_value.get("zoneKey")
                    or key
                ),
                country_code=(
                    str(raw_value["countryCode"]).upper() if raw_value.get("countryCode") else None
                ),
                accessible_endpoints=sorted(access),
            )
        )
    return sorted(zones, key=lambda item: item.zone)


async def test_electricity_maps(
    provider: ElectricityMapsProvider,
    *,
    max_zones: int,
) -> ElectricityMapsTestResult:
    try:
        raw_zones = await provider.list_zones()
    except ElectricityMapsProviderError as error:
        raise _provider_error(error) from error
    zones = _zone_access_items(raw_zones)
    return ElectricityMapsTestResult(
        authenticated=True,
        accessible_zone_count=len(zones),
        zones=zones[:max_zones],
        zones_truncated=len(zones) > max_zones,
    )


def _supports_carbon_range(zone: ElectricityMapsZoneAccess) -> bool:
    routes = {route.strip("/") for route in zone.accessible_endpoints}
    return "*" in routes or "carbon-intensity/past-range" in routes


async def _resolve_local_zone(
    session: AsyncSession,
    *,
    site: Site,
    requested_zone: str | None,
) -> tuple[str | None, GridZoneResolution | None, DataSource | None]:
    data_source = await repository.get_data_source(
        session,
        company_id=site.company_id,
        site_id=site.id,
    )
    if requested_zone:
        return requested_zone, "request", data_source
    if data_source is not None:
        cached_zone = data_source.configuration.get("zone")
        if isinstance(cached_zone, str) and cached_zone:
            return cached_zone.upper(), "cached", data_source

    configured_zone = await repository.get_site_zone_hint(
        session,
        company_id=site.company_id,
        site_id=site.id,
    )
    if configured_zone is not None:
        return configured_zone, "configured", data_source

    return None, None, data_source


async def _resolve_provider_zone(
    *,
    site: Site,
    provider: ElectricityMapsProvider,
) -> tuple[str, GridZoneResolution]:
    """Resolve an unconfigured site without holding a database transaction open."""

    try:
        zones = _zone_access_items(await provider.list_zones())
    except ElectricityMapsProviderError as error:
        raise _provider_error(error) from error
    candidates = [
        zone
        for zone in zones
        if zone.country_code == site.country_code and _supports_carbon_range(zone)
    ]
    exact = [zone for zone in candidates if zone.zone == site.country_code]
    if len(exact) == 1:
        return exact[0].zone, "country_exact"
    if len(candidates) == 1:
        return candidates[0].zone, "country_unique"
    if len(candidates) > 1:
        raise ZoneResolutionError(
            "Multiple accessible grid zones match this site's country; provide a zone explicitly.",
            field_details={"zone": "required_for_ambiguous_country"},
        )
    raise ZoneResolutionError(
        "No accessible Electricity Maps zone could be resolved for this site.",
        field_details={"site_id": str(site.id)},
    )


def _sync_range(request: GridIntensitySyncRequest) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    if request.start is not None and request.end is not None:
        start, end = request.start, request.end
    else:
        end = now
        start = end - timedelta(hours=request.lookback_hours)
    if end - start > MAX_HOURLY_RANGE:
        raise IntegrationServiceError(
            "The hourly sync range cannot exceed 240 hours.",
            field_details={"range": "maximum_240_hours"},
        )
    if end > now + timedelta(minutes=1):
        raise IntegrationServiceError(
            "Grid intensity sync only accepts past ranges.",
            field_details={"end": "must_not_be_in_future"},
        )
    return start, end


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _provider_version(point_datetime: datetime, updated_at: datetime | None) -> str:
    point_part = point_datetime.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    update_part = (updated_at or point_datetime).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{point_part}-U{update_part}"


async def sync_grid_intensity(
    session: AsyncSession,
    *,
    company_id: UUID,
    site_id: UUID,
    request: GridIntensitySyncRequest,
    provider: ElectricityMapsProvider,
) -> GridIntensitySyncResult:
    site = await repository.get_site(session, company_id=company_id, site_id=site_id)
    if site is None:
        raise SiteNotFoundError("The requested site was not found.")

    zone, zone_resolution, data_source = await _resolve_local_zone(
        session,
        site=site,
        requested_zone=request.zone,
    )
    # Site and cached-zone reads are complete. Release the database transaction
    # before either bounded provider call so network latency does not hold a
    # PostgreSQL connection or snapshot open.
    await session.commit()
    if zone is None or zone_resolution is None:
        zone, zone_resolution = await _resolve_provider_zone(site=site, provider=provider)
    start, end = _sync_range(request)
    try:
        raw_payload = dict(
            await provider.get_carbon_intensity_range(
                zone=zone,
                start=start,
                end=end,
                disable_estimations=request.disable_estimations,
            )
        )
    except ElectricityMapsProviderError as error:
        raise _provider_error(error) from error

    try:
        payload = ElectricityMapsRangePayload.model_validate(raw_payload)
    except ValidationError as error:
        raise InvalidProviderResponseError(
            "Electricity Maps returned carbon intensity data with an invalid shape."
        ) from error
    if payload.zone.upper() != zone:
        raise InvalidProviderResponseError("Electricity Maps returned data for a different zone.")
    if not payload.data:
        raise GridIntensityNotFoundError("No grid intensity points exist in the requested range.")
    if len(payload.data) > MAX_HOURLY_POINTS:
        raise InvalidProviderResponseError(
            "Electricity Maps returned more than the bounded hourly point limit."
        )
    point_timestamps = {point.datetime for point in payload.data}
    if len(point_timestamps) != len(payload.data):
        raise InvalidProviderResponseError("Electricity Maps returned duplicate hourly timestamps.")
    if any(not (start <= point.datetime < end) for point in payload.data):
        raise InvalidProviderResponseError(
            "Electricity Maps returned a point outside the requested range."
        )
    if payload.temporal_granularity != "hourly" or any(
        point.temporal_granularity != "hourly" for point in payload.data
    ):
        raise InvalidProviderResponseError(
            "Electricity Maps returned data at an unexpected temporal granularity."
        )
    if any(point.zone is not None and point.zone.upper() != zone for point in payload.data):
        raise InvalidProviderResponseError(
            "Electricity Maps returned a point for a different zone."
        )
    if any(
        point.emission_factor_type != "lifecycle" or not point.flow_traced for point in payload.data
    ):
        raise InvalidProviderResponseError(
            "Electricity Maps returned data for an unexpected factor method."
        )
    if request.disable_estimations and any(point.is_estimated for point in payload.data):
        raise InvalidProviderResponseError(
            "Electricity Maps returned estimated data despite estimations being disabled."
        )

    is_synthetic_provider = (
        getattr(provider, "is_synthetic", False) is True
        or raw_payload.get("synthetic") is True
    )
    # The provider call intentionally happens outside a transaction. Serialize
    # only the persistence phase so concurrent syncs deterministically read back
    # the rows committed by the first caller instead of racing unique keys.
    await repository.acquire_grid_history_lock(session, company_id=site.company_id)
    data_source = await repository.get_data_source(
        session,
        company_id=site.company_id,
        site_id=site.id,
        is_synthetic=is_synthetic_provider,
    )
    if data_source is None:
        fixture_suffix = " (synthetic fixture)" if is_synthetic_provider else ""
        data_source = DataSource(
            company_id=site.company_id,
            site_id=site.id,
            name=f"Electricity Maps grid intensity - {site.code}{fixture_suffix}",
            source_type="synthetic" if is_synthetic_provider else "api",
            status="ready",
            external_reference=repository.ELECTRICITY_MAPS_EXTERNAL_REFERENCE,
            configuration={},
            is_synthetic=is_synthetic_provider,
        )
        session.add(data_source)
    data_source.status = "ready"
    data_source.is_synthetic = is_synthetic_provider
    data_source.source_type = "synthetic" if is_synthetic_provider else "api"
    data_source.configuration = {
        "provider": "electricity_maps",
        "provider_mode": "fixture" if is_synthetic_provider else "live",
        "api_version": "v4",
        "zone": zone,
        "endpoint": "/carbon-intensity/past-range",
        "temporal_granularity": "hourly",
        "emission_factor_type": "lifecycle",
        "flow_traced": True,
    }
    await session.flush()

    snapshot = SnapshotEnvelope(
        site_id=site.id,
        requested_start=start,
        requested_end=end,
        provider_mode="fixture" if is_synthetic_provider else "live",
        synthetic=is_synthetic_provider,
        response=raw_payload,
    ).model_dump(mode="json")
    snapshot_bytes = _json_bytes(snapshot)
    response_checksum = hashlib.sha256(snapshot_bytes).hexdigest()
    source_document = await repository.get_source_document_by_checksum(
        session,
        company_id=site.company_id,
        checksum=response_checksum,
    )
    if source_document is None:
        source_document = SourceDocument(
            company_id=site.company_id,
            data_source_id=data_source.id,
            filename=(f"electricity-maps-{zone}-{start:%Y%m%dT%H%M%SZ}-{end:%Y%m%dT%H%M%SZ}.json"),
            content_type="application/json",
            checksum=response_checksum,
            version=1,
            size_bytes=len(snapshot_bytes),
            storage_uri=f"database://electricity-maps/{response_checksum}",
            document_metadata={
                "provider": "electricity_maps",
                "provider_mode": "fixture" if is_synthetic_provider else "live",
                "synthetic": is_synthetic_provider,
                "api_version": "v4",
                "endpoint": "/carbon-intensity/past-range",
                "zone": zone,
                "requested_start": start.isoformat(),
                "requested_end": end.isoformat(),
                "response_snapshot": snapshot,
            },
        )
        session.add(source_document)
        await session.flush()

    metric = await repository.get_metric_definition(session, company_id=site.company_id)
    if metric is None:
        metric = MetricDefinition(
            company_id=site.company_id,
            key=repository.GRID_INTENSITY_METRIC_KEY,
            version=repository.GRID_INTENSITY_METRIC_VERSION,
            name="Electricity grid carbon intensity",
            canonical_unit="gCO2e/kWh",
            dimensions={"site": True, "geography": "electricity_maps_zone", "time": "hourly"},
            handler="integrations.electricity_maps",
            method_version="electricity-maps-v4",
            description="Flow-traced lifecycle grid carbon intensity cached from Electricity Maps.",
            is_active=True,
        )
        session.add(metric)
        await session.flush()

    inserted_points = 0
    existing_points = 0
    estimated_points = 0
    prepared = []
    for point in sorted(payload.data, key=lambda item: item.datetime):
        point_json = point.model_dump(mode="json", by_alias=True)
        point_bytes = _json_bytes(point_json)
        point_checksum = hashlib.sha256(point_bytes).hexdigest()
        locator = f"carbon-intensity:{point.datetime.isoformat()}"
        point_version = _provider_version(point.datetime, point.updated_at)
        method_version = f"{repository.GRID_POINT_METHOD_PREFIX}:{point_version}"
        prepared.append((point, point_bytes, point_checksum, locator, method_version))
    # The response is bounded before persistence. Read existing dependencies in
    # two batches, then append evidence with server-generated IDs correlated by
    # locator. Ordered ORM RETURNING would otherwise insert one hour at a time.
    evidence_by_locator = await repository.get_evidence_items(
        session, company_id=site.company_id, source_document_id=source_document.id,
        locators=[item[3] for item in prepared],
    )
    existing_by_version = await repository.get_grid_intensity_points(
        session, company_id=site.company_id, site_id=site.id, zone=zone,
        versions=[(item[0].datetime, item[0].temporal_granularity, item[4]) for item in prepared],
    )
    new_evidence = []
    for point, point_bytes, point_checksum, locator, _method_version in prepared:
        evidence = evidence_by_locator.get(locator)
        if evidence is None:
            evidence = EvidenceItem(
                company_id=site.company_id,
                source_document_id=source_document.id,
                evidence_type="api_snapshot",
                locator=locator,
                content_text=point_bytes.decode("utf-8"),
                checksum=point_checksum,
                evidence_metadata={
                    "provider": "electricity_maps",
                    "provider_mode": "fixture" if is_synthetic_provider else "live",
                    "synthetic": is_synthetic_provider,
                    "api_version": "v4",
                    "endpoint": "/carbon-intensity/past-range",
                    "zone": zone,
                    "provider_datetime": point.datetime.isoformat(),
                    "provider_updated_at": (
                        point.updated_at.isoformat() if point.updated_at is not None else None
                    ),
                    "provider_value_gco2eq_per_kwh": str(point.carbon_intensity),
                    "is_estimated": point.is_estimated,
                    "emission_factor_type": point.emission_factor_type,
                    "temporal_granularity": point.temporal_granularity,
                },
            )
            new_evidence.append(evidence)
            evidence_by_locator[locator] = evidence
    if new_evidence:
        added = await session.execute(
            insert(EvidenceItem).returning(EvidenceItem.id, EvidenceItem.locator),
            [{name: getattr(item, name) for name in (
                "company_id", "source_document_id", "evidence_type", "locator",
                "content_text", "checksum", "evidence_metadata",
            )} for item in new_evidence],
        )
        for row in added:
            evidence_by_locator[row.locator].id = row.id

    new_points = []
    for point, _point_bytes, point_checksum, locator, method_version in prepared:
        estimated_points += int(point.is_estimated)
        evidence = evidence_by_locator[locator]
        existing = existing_by_version.get(
            (point.datetime, point.temporal_granularity, method_version)
        )
        canonical_kg_intensity = (point.carbon_intensity * KG_PER_GRAM).quantize(
            KG_INTENSITY_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        if existing is not None:
            if existing.point_hash != point_checksum:
                raise InvalidProviderResponseError(
                    "Electricity Maps reused a provider point version with different content."
                )
            existing_points += 1
            continue
        new_points.append(
            GridIntensityPoint(
                company_id=site.company_id,
                site_id=site.id,
                source_document_id=source_document.id,
                evidence_item_id=evidence.id,
                metric_definition_id=metric.id,
                provider="electricity_maps",
                zone=zone,
                observed_at=point.datetime,
                provider_updated_at=point.updated_at,
                temporal_granularity=point.temporal_granularity,
                emission_factor_type=point.emission_factor_type,
                flow_traced=point.flow_traced,
                is_estimated=point.is_estimated,
                intensity_gco2e_per_kwh=point.carbon_intensity,
                method_version=method_version,
                point_hash=point_checksum,
                provider_metadata={
                    "api_version": "v4",
                    "endpoint": "/carbon-intensity/past-range",
                    "created_at": (
                        point.created_at.isoformat() if point.created_at is not None else None
                    ),
                    "estimation_method": point.estimation_method,
                    "canonical_kgco2e_per_kwh": str(canonical_kg_intensity),
                    "response_checksum": response_checksum,
                    "synthetic": bool(data_source.is_synthetic),
                },
            )
        )
        inserted_points += 1

    if new_points:
        await session.execute(insert(GridIntensityPoint), [
            {name: getattr(point, name) for name in (
                "company_id", "site_id", "source_document_id", "evidence_item_id",
                "metric_definition_id", "provider", "zone", "observed_at", "provider_updated_at",
                "temporal_granularity", "emission_factor_type", "flow_traced", "is_estimated",
                "intensity_gco2e_per_kwh", "method_version", "point_hash", "provider_metadata",
            )} for point in new_points
        ])
    await session.commit()
    return GridIntensitySyncResult(
        site_id=site.id,
        zone=zone,
        zone_resolution=zone_resolution,
        requested_start=start,
        requested_end=end,
        received_points=len(payload.data),
        inserted_points=inserted_points,
        existing_points=existing_points,
        estimated_points=estimated_points,
        data_source_id=data_source.id,
        source_document_id=source_document.id,
        response_checksum=response_checksum,
    )


async def get_latest_grid_intensity(
    session: AsyncSession,
    *,
    company_id: UUID,
    site_id: UUID,
) -> LatestGridIntensityResult:
    site = await repository.get_site(session, company_id=company_id, site_id=site_id)
    if site is None:
        raise SiteNotFoundError("The requested site was not found.")

    data_source = await repository.get_data_source(
        session,
        company_id=company_id,
        site_id=site_id,
    )
    zone: str | None = None
    if data_source is not None:
        configured_zone = data_source.configuration.get("zone")
        if isinstance(configured_zone, str) and configured_zone.strip():
            zone = configured_zone.strip().upper()
    if zone is None:
        zone = await repository.get_site_zone_hint(
            session,
            company_id=company_id,
            site_id=site_id,
        )
    if zone is None:
        raise GridIntensityNotFoundError(
            "No cached Electricity Maps zone mapping exists for this site."
        )

    record = await repository.get_latest_grid_intensity_point(
        session,
        company_id=company_id,
        site_id=site_id,
        zone=zone,
    )
    if record is None:
        raise GridIntensityNotFoundError("No cached grid intensity exists for this site.")
    point, evidence, document, data_source = record

    return LatestGridIntensityResult(
        site_id=site.id,
        grid_intensity_point_id=point.id,
        zone=point.zone,
        value=(point.intensity_gco2e_per_kwh * KG_PER_GRAM).quantize(
            KG_INTENSITY_QUANTUM,
            rounding=ROUND_HALF_UP,
        ),
        unit="kgCO2e/kWh",
        provider_value_gco2eq_per_kwh=_trim_decimal_scale(
            point.intensity_gco2e_per_kwh
        ),
        provider_timestamp=point.observed_at,
        provider_updated_at=point.provider_updated_at,
        is_estimated=point.is_estimated,
        emission_factor_type=point.emission_factor_type,
        temporal_granularity=point.temporal_granularity,
        provenance=GridIntensityProvenance(
            provider_mode=(
                "fixture" if data_source.is_synthetic else "live"
            ),
            synthetic=data_source.is_synthetic,
            endpoint=str(data_source.configuration.get("endpoint", "")),
            source_site_id=data_source.site_id,
            source_document_id=document.id,
            evidence_item_id=evidence.id,
            response_checksum=document.checksum,
            retrieved_at=document.imported_at,
        ),
    )
