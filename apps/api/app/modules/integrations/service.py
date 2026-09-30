from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import EmissionFactor
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
FACTOR_QUANTUM = Decimal("0.000000000001")


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
    if error.code == "integration_not_configured":
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

    if data_source is None:
        data_source = DataSource(
            company_id=site.company_id,
            site_id=site.id,
            name=f"Electricity Maps grid intensity - {site.code}",
            source_type="api",
            status="ready",
            external_reference=repository.ELECTRICITY_MAPS_EXTERNAL_REFERENCE,
            configuration={},
            is_synthetic=False,
        )
        session.add(data_source)
    data_source.status = "ready"
    data_source.configuration = {
        "provider": "electricity_maps",
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
            canonical_unit="kgCO2e/kWh",
            dimensions={"site": True, "geography": "electricity_maps_zone", "time": "hourly"},
            handler="integrations.electricity_maps",
            method_version="electricity-maps-v4",
            description="Flow-traced lifecycle grid carbon intensity cached from Electricity Maps.",
            is_active=True,
        )
        session.add(metric)
        await session.flush()

    inserted_factors = 0
    existing_factors = 0
    estimated_points = 0
    for point in sorted(payload.data, key=lambda item: item.datetime):
        estimated_points += int(point.is_estimated)
        point_json = point.model_dump(mode="json", by_alias=True)
        point_bytes = _json_bytes(point_json)
        point_checksum = hashlib.sha256(point_bytes).hexdigest()
        locator = f"carbon-intensity:{point.datetime.isoformat()}"
        evidence = await repository.get_evidence_item(
            session,
            company_id=site.company_id,
            source_document_id=source_document.id,
            locator=locator,
        )
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
            session.add(evidence)
            await session.flush()

        version = _provider_version(point.datetime, point.updated_at)
        existing = await repository.get_emission_factor(
            session,
            company_id=site.company_id,
            version=version,
            geography=zone,
        )
        factor_value = (point.carbon_intensity * KG_PER_GRAM).quantize(
            FACTOR_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        if existing is not None:
            if existing.factor_value != factor_value:
                raise InvalidProviderResponseError(
                    "Electricity Maps reused a provider version with a different value."
                )
            existing_factors += 1
            continue
        session.add(
            EmissionFactor(
                company_id=site.company_id,
                metric_definition_id=metric.id,
                evidence_item_id=evidence.id,
                factor_code=repository.GRID_FACTOR_CODE,
                version=version,
                name=f"Electricity Maps grid intensity {zone} {point.datetime.isoformat()}",
                geography=zone,
                factor_value=factor_value,
                numerator_unit="kgCO2e",
                denominator_unit="kWh",
                effective_from=point.datetime.date(),
                effective_to=point.datetime.date(),
                source_quality=Decimal("0.85000") if point.is_estimated else Decimal("0.95000"),
                factor_specificity=Decimal("1.00000"),
                factor_recency=Decimal("1.00000"),
                status="active",
            )
        )
        inserted_factors += 1

    await session.commit()
    return GridIntensitySyncResult(
        site_id=site.id,
        zone=zone,
        zone_resolution=zone_resolution,
        requested_start=start,
        requested_end=end,
        received_points=len(payload.data),
        inserted_factors=inserted_factors,
        existing_factors=existing_factors,
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

    record = await repository.get_latest_grid_factor(
        session,
        company_id=company_id,
        zone=zone,
    )
    if record is None:
        raise GridIntensityNotFoundError("No cached grid intensity exists for this site.")
    factor, evidence, document, data_source = record
    metadata = evidence.evidence_metadata
    try:
        provider_timestamp = datetime.fromisoformat(str(metadata["provider_datetime"]))
        provider_updated_at = (
            datetime.fromisoformat(str(metadata["provider_updated_at"]))
            if metadata.get("provider_updated_at")
            else None
        )
        provider_value = Decimal(str(metadata["provider_value_gco2eq_per_kwh"]))
    except (KeyError, ValueError, TypeError) as error:
        raise InvalidProviderResponseError(
            "Cached grid intensity provenance is incomplete."
        ) from error

    return LatestGridIntensityResult(
        site_id=site.id,
        factor_id=factor.id,
        zone=factor.geography,
        value=factor.factor_value,
        unit=f"{factor.numerator_unit}/{factor.denominator_unit}",
        provider_value_gco2eq_per_kwh=provider_value,
        provider_timestamp=provider_timestamp,
        provider_updated_at=provider_updated_at,
        is_estimated=bool(metadata.get("is_estimated", False)),
        emission_factor_type=str(metadata.get("emission_factor_type", "lifecycle")),
        temporal_granularity=str(metadata.get("temporal_granularity", "hourly")),
        provenance=GridIntensityProvenance(
            endpoint=str(data_source.configuration.get("endpoint", "")),
            source_site_id=data_source.site_id,
            source_document_id=document.id,
            evidence_item_id=evidence.id,
            response_checksum=document.checksum,
            retrieved_at=document.imported_at,
        ),
    )
