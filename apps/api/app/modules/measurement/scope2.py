"""Hourly Scope 2 execution with exact, evidence-bound interval alignment."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.modules.imports.schemas import HourlyElectricityRow
from app.modules.integrations.schemas import ElectricityMapsIntensityPoint, SnapshotEnvelope
from app.modules.measurement.domain import (
    CONFIDENCE_V2_WEIGHTS,
    MeasurementDomainError,
    calculate_confidence_v2,
    calculate_emissions,
    calculate_scope2_emissions,
    calculate_variance,
    canonical_json_value,
    hourly_timestamp,
    measurement_code_hash,
    normalize_energy_to_kwh,
    sha256_payload,
    weighted_average,
)
from app.modules.measurement.repository import (
    MAX_CALCULATION_ACTIVITY_ROWS,
    GridCalculationPersistenceItem,
    MeasurementPersistencePlan,
)
from app.modules.measurement.schemas import MeasurementCalculateRequest, MeasurementResult

if TYPE_CHECKING:
    from app.modules.measurement.service import MeasurementService


async def calculate_scope2(
    service: MeasurementService,
    request: MeasurementCalculateRequest,
) -> MeasurementResult:
    repository = service.repository
    trace_id = request.trace_id or f"measurement-{uuid4()}"

    def fail(code: str, message: str, *, status: int = 422, state="failed_validation", **details):
        return service._error(
            status_code=status,
            code=code,
            message=message,
            terminal_state=state,
            trace_id=trace_id,
            field_details=details,
        )

    if (
        request.activity_metric_key != "activity.electricity_consumption"
        or request.method_key != "measurement.scope2.location_based.hourly"
    ):
        raise fail(
            "scope2_context_mismatch", "Scope 2 requires the hourly electricity metric and method."
        )
    context = await service._resolve_context(request, trace_id=trace_id)
    if request.actor_id is not None and not await repository.actor_exists(
        company_id=request.company_id,
        actor_id=request.actor_id,
    ):
        raise service._context_missing("actor_id", request.actor_id, trace_id)
    if request.agent_run_id is not None and not await repository.agent_run_exists(
        company_id=request.company_id,
        agent_run_id=request.agent_run_id,
    ):
        raise service._context_missing("agent_run_id", request.agent_run_id, trace_id)

    config = context.method.configuration
    try:
        if (
            context.method.version != "2.0.0"
            or not isinstance(config, dict)
            or config.get("confidence_version", "2.0.0") != "2.0.0"
            or config.get("rounding_policy", "ROUND_HALF_EVEN") != "ROUND_HALF_EVEN"
            or config.get("output_scale", 6) != 6
            or config.get("missing_interval_policy", "fail_closed") != "fail_closed"
        ):
            raise ValueError
        weights = {
            key: Decimal(str(value))
            for key, value in config.get("confidence_weights", CONFIDENCE_V2_WEIGHTS).items()
        }
        if weights != CONFIDENCE_V2_WEIGHTS:
            raise ValueError
        measured_quality = Decimal(str(config.get("measured_source_quality", "0.95")))
        estimated_quality = Decimal(str(config.get("estimated_source_quality", "0.70")))
        calculate_confidence_v2(
            source_quality=measured_quality,
            method_fit=Decimal(1),
            temporal_match=Decimal(1),
            completeness=estimated_quality,
        )
    except (AttributeError, InvalidOperation, TypeError, ValueError) as error:
        raise fail(
            "invalid_measurement_method",
            "Scope 2 requires the versioned confidence v2 method.",
            status=500,
        ) from error

    site_zone = getattr(context.site, "electricity_maps_zone", None)
    zone = request.grid_zone or site_zone
    if not zone:
        raise fail(
            "grid_zone_required",
            "The site must have an explicit resolved grid zone.",
            status=409,
            state="needs_clarification",
        )
    if site_zone and zone != site_zone:
        raise fail("grid_zone_mismatch", "The requested grid zone does not match the site.")

    await repository.acquire_calculation_lock(
        company_id=request.company_id,
        site_id=request.site_id,
        reporting_period_id=request.reporting_period_id,
        metric_definition_id=context.output_metric.id,
        material_code=request.material_code,
    )
    activities = await repository.list_activities(
        company_id=request.company_id,
        site_id=request.site_id,
        reporting_period_id=request.reporting_period_id,
        metric_definition_id=context.activity_metric.id,
        material_code=request.material_code,
        activity_record_ids=request.activity_record_ids,
    )
    if not activities:
        raise fail(
            "no_activity_data",
            "No valid hourly electricity records match the context.",
            status=404,
            state="no_data",
        )
    if len(activities) > MAX_CALCULATION_ACTIVITY_ROWS:
        raise fail(
            "activity_row_limit_exceeded", "The hourly measurement exceeds the 3000-row limit."
        )
    if request.activity_record_ids and set(request.activity_record_ids) != {
        source.activity.id for source in activities
    }:
        raise fail(
            "activity_scope_mismatch", "Requested activities are missing or outside the context."
        )
    if await repository.has_blocking_hourly_issues(
        company_id=request.company_id,
        source_document_ids={source.raw_activity.source_document_id for source in activities},
        data_source_ids={source.raw_activity.data_source_id for source in activities},
    ):
        raise fail(
            "activity_quality_blocked", "Hourly source data contains unresolved structural errors."
        )

    by_hour = {}
    quantities = {}
    for source in activities:
        activity = source.activity
        try:
            observed_at = hourly_timestamp(source.raw_activity.raw_payload)
            raw_row = HourlyElectricityRow.model_validate(source.raw_activity.raw_payload)
            quantity = normalize_energy_to_kwh(activity.quantity, activity.unit)
            stored = normalize_energy_to_kwh(activity.normalized_quantity, activity.normalized_unit)
            raw_quantity = normalize_energy_to_kwh(raw_row.quantity, raw_row.unit)
        except (ValueError, TypeError) as error:
            raise fail(
                "invalid_hourly_activity",
                "An electricity source row is invalid for exact hourly calculation.",
                activity_record_id=str(activity.id),
            ) from error
        raw_checksum = hashlib.sha256(
            json.dumps(
                source.raw_activity.raw_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if raw_checksum != source.raw_activity.checksum:
            raise fail(
                "activity_checksum_mismatch", "A raw activity row differs from its source checksum."
            )
        if quantity != stored or quantity != raw_quantity:
            raise fail(
                "normalized_quantity_mismatch",
                "Stored electricity differs from exact conversion.",
                activity_record_id=str(activity.id),
            )
        if (
            not context.reporting_period.start_date
            <= observed_at.date()
            <= context.reporting_period.end_date
        ):
            raise fail(
                "activity_period_mismatch", "An hourly timestamp is outside the reporting period."
            )
        if activity.activity_date is not None and activity.activity_date != observed_at.date():
            raise fail(
                "activity_timestamp_mismatch", "The activity date differs from its UTC timestamp."
            )
        if observed_at in by_hour:
            raise fail(
                "duplicate_activity_interval",
                "Multiple electricity rows cover the same UTC hour.",
                timestamp=observed_at.isoformat(),
            )
        by_hour[observed_at] = source
        quantities[observed_at] = quantity
    hours = sorted(by_hour)
    if any(right - left != timedelta(hours=1) for left, right in pairwise(hours)):
        raise fail(
            "missing_activity_interval",
            "Hourly electricity contains a gap; interpolation is prohibited.",
        )

    points = await repository.list_grid_points(
        company_id=request.company_id,
        site_id=request.site_id,
        zone=zone,
        start=hours[0],
        end=hours[-1],
        method_version=request.grid_method_version,
    )
    if len(points) > MAX_CALCULATION_ACTIVITY_ROWS * 2:
        raise fail(
            "grid_candidate_limit_exceeded",
            "Narrow the immutable grid method version.",
            status=409,
            state="needs_clarification",
        )
    grid_by_hour = defaultdict(list)
    for point in points:
        if point.observed_at.utcoffset() is None:
            raise fail("invalid_grid_timestamp", "A grid timestamp has no UTC offset.")
        grid_by_hour[point.observed_at.astimezone(UTC)].append(point)
    selected = []
    for hour in hours:
        matches = grid_by_hour[hour]
        if not matches:
            raise fail(
                "missing_grid_interval",
                "An exact hourly grid point is unavailable; no interpolation is allowed.",
                state="no_data",
                timestamp=hour.isoformat(),
            )
        if len(matches) != 1:
            raise fail(
                "ambiguous_grid_interval",
                "Multiple immutable grid versions match an hour; select a method version.",
                status=409,
                state="needs_clarification",
                timestamp=hour.isoformat(),
            )
        selected.append(matches[0])
    evidence = await repository.get_grid_evidence(
        company_id=request.company_id,
        evidence_item_ids={point.evidence_item_id for point in selected},
    )
    if any(
        point.evidence_item_id is None or point.evidence_item_id not in evidence
        for point in selected
    ):
        raise fail(
            "grid_evidence_missing", "A selected grid point lacks accessible supporting evidence."
        )
    validated_snapshots = {}
    for point in selected:
        item, document = evidence[point.evidence_item_id]
        try:
            provider_point = ElectricityMapsIntensityPoint.model_validate_json(item.content_text)
            if document.id not in validated_snapshots:
                snapshot_data = document.document_metadata["response_snapshot"]
                snapshot = SnapshotEnvelope.model_validate(snapshot_data)
                snapshot_hash = hashlib.sha256(
                    json.dumps(
                        snapshot_data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                    ).encode("utf-8")
                ).hexdigest()
                if snapshot_hash != document.checksum or snapshot.site_id != request.site_id:
                    raise ValueError
                validated_snapshots[document.id] = snapshot
            snapshot = validated_snapshots[document.id]
            if (
                item.source_document_id != point.source_document_id
                or hashlib.sha256(item.content_text.encode("utf-8")).hexdigest() != item.checksum
                or item.checksum != point.point_hash
                or provider_point.datetime != point.observed_at
                or str(snapshot.response.get("zone", "")).upper() != point.zone
                or (provider_point.zone is not None and provider_point.zone.upper() != point.zone)
                or provider_point.updated_at != point.provider_updated_at
                or provider_point.carbon_intensity != point.intensity_gco2e_per_kwh
                or provider_point.is_estimated != point.is_estimated
                or provider_point.flow_traced != point.flow_traced
                or provider_point.emission_factor_type != point.emission_factor_type
                or provider_point.temporal_granularity != "hourly"
            ):
                raise ValueError
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise fail(
                "grid_evidence_mismatch",
                "A grid point differs from its immutable provider evidence.",
            ) from error

    items = []
    input_rows = []
    for hour, point in zip(hours, selected, strict=True):
        source = by_hour[hour]
        quantity = quantities[hour]
        try:
            emissions = calculate_scope2_emissions(quantity, point.intensity_gco2e_per_kwh)
        except MeasurementDomainError as error:
            raise fail(
                "measurement_value_out_of_range", "Scope 2 emissions exceed the numeric range."
            ) from error
        formula = (
            f"{quantity:f} kWh * {point.intensity_gco2e_per_kwh:f} gCO2e/kWh / 1000 = "
            f"{emissions:f} kgCO2e"
        )
        calculation_payload = {
            "activity_record_id": source.activity.id,
            "grid_intensity_point_id": point.id,
            "interval_start": hour,
            "normalized_quantity_kwh": quantity,
            "intensity_gco2e_per_kwh": point.intensity_gco2e_per_kwh,
            "emissions_kgco2e": emissions,
            "formula": formula,
        }
        items.append(
            GridCalculationPersistenceItem(
                source=source,
                grid_point=point,
                interval_start=hour,
                normalized_quantity_kwh=quantity,
                emissions_kgco2e=emissions,
                record_completeness=Decimal(1),
                formula=formula,
                output_hash=sha256_payload(calculation_payload),
            )
        )
        input_rows.append(
            {
                **calculation_payload,
                "raw_activity_record_id": source.raw_activity.id,
                "raw_checksum": source.raw_activity.checksum,
                "source_document_id": source.raw_activity.source_document_id,
                "data_source_id": source.raw_activity.data_source_id,
                "source_row_key": source.raw_activity.row_key,
                "source_quantity": source.activity.quantity,
                "source_unit": source.activity.unit,
                "grid_point_hash": point.point_hash,
                "grid_method_version": point.method_version,
                "grid_provider": point.provider,
                "grid_zone": point.zone,
                "grid_is_estimated": point.is_estimated,
                "grid_flow_traced": point.flow_traced,
                "grid_emission_factor_type": point.emission_factor_type,
                "grid_source_document_id": point.source_document_id,
                "grid_evidence_item_id": point.evidence_item_id,
                "grid_evidence_checksum": evidence[point.evidence_item_id][0].checksum,
                "grid_source_document_checksum": evidence[point.evidence_item_id][1].checksum,
            }
        )
    try:
        total = calculate_emissions(
            sum((item.emissions_kgco2e for item in items), Decimal(0)), Decimal(1)
        )
    except MeasurementDomainError as error:
        raise fail(
            "measurement_value_out_of_range", "Total Scope 2 exceeds the numeric range."
        ) from error
    reporting_hours = (
        (context.reporting_period.end_date - context.reporting_period.start_date).days + 1
    ) * 24
    period_fraction = (Decimal(len(hours)) / Decimal(reporting_hours)).quantize(Decimal("0.00001"))
    coverage = {
        "interval_start": hours[0],
        "interval_end": hours[-1] + timedelta(hours=1),
        "observed_hours": len(hours),
        "reporting_period_hours": reporting_hours,
        "reporting_period_fraction": period_fraction,
        "full_reporting_period": len(hours) == reporting_hours,
    }
    confidence = calculate_confidence_v2(
        source_quality=weighted_average(
            (
                estimated_quality if item.grid_point.is_estimated else measured_quality,
                item.normalized_quantity_kwh,
            )
            for item in items
        ),
        method_fit=Decimal(1),
        temporal_match=Decimal(1),
        completeness=period_fraction,
    )
    baseline = await repository.get_baseline(
        company_id=request.company_id,
        site_id=request.site_id,
        reporting_period_id=request.reporting_period_id,
        metric_definition_id=context.output_metric.id,
    )
    snapshot = canonical_json_value(
        {
            "company_id": request.company_id,
            "site_id": request.site_id,
            "reporting_period_id": request.reporting_period_id,
            "activity_metric_definition_id": context.activity_metric.id,
            "output_metric_definition_id": context.output_metric.id,
            "activity_metric_version": context.activity_metric.version,
            "output_metric_version": context.output_metric.version,
            "method_definition_id": context.method.id,
            "method_key": context.method.key,
            "method_version": context.method.version,
            "code_version": context.method.code_version,
            "code_hash": measurement_code_hash(),
            "method_configuration": config,
            "material_code": request.material_code,
            "grid_zone": zone,
            "activities": input_rows,
            "confidence": asdict(confidence),
            "coverage": coverage,
            "baseline": (
                {
                    "id": baseline.id,
                    "frozen_hash": baseline.frozen_hash,
                    "value_kgco2e": baseline.value_kgco2e,
                }
                if baseline
                else None
            ),
        }
    )
    input_hash = sha256_payload(snapshot)
    existing = await repository.find_measurement_id_by_input_hash(
        company_id=request.company_id, input_hash=input_hash
    )
    if existing is not None:
        detail = await service.get_measurement(
            company_id=request.company_id, measurement_id=existing, trace_id=trace_id
        )
        return MeasurementResult(**detail.model_dump(), trace_id=trace_id, idempotent=True)
    output_hash = sha256_payload(
        {
            "input_hash": input_hash,
            "value_kgco2e": total,
            "confidence": asdict(confidence),
            "calculation_hashes": [item.output_hash for item in items],
        }
    )
    previous = await repository.get_latest_verified_measurement(
        company_id=request.company_id,
        site_id=request.site_id,
        reporting_period_id=request.reporting_period_id,
        metric_definition_id=context.output_metric.id,
        material_code=request.material_code,
    )
    plan = MeasurementPersistencePlan(
        company_id=request.company_id,
        actor_id=request.actor_id,
        agent_run_id=request.agent_run_id,
        trace_id=trace_id,
        material_code=request.material_code,
        context=context,
        input_hash=input_hash,
        output_hash=output_hash,
        rounding_policy="ROUND_HALF_EVEN",
        formula="sum(kWh * gCO2e_per_kWh / 1000)",
        value_kgco2e=total,
        confidence=confidence,
        items=tuple(items),
        baseline=baseline,
        variance=calculate_variance(total, baseline.value_kgco2e) if baseline else None,
        supersedes_measurement=previous,
        input_snapshot=snapshot,
    )
    try:
        persisted = await repository.persist_measurement(plan)
        await service.session.commit()
    except IntegrityError as error:
        await service.session.rollback()
        existing = await repository.find_measurement_id_by_input_hash(
            company_id=request.company_id, input_hash=input_hash
        )
        if existing is None:
            raise fail(
                "measurement_persistence_conflict",
                "Scope 2 conflicted with an immutable result.",
                status=409,
            ) from error
        detail = await service.get_measurement(
            company_id=request.company_id, measurement_id=existing, trace_id=trace_id
        )
        return MeasurementResult(**detail.model_dump(), trace_id=trace_id, idempotent=True)
    except BaseException:
        await service.session.rollback()
        raise
    detail = await service.get_measurement(
        company_id=request.company_id, measurement_id=persisted.measurement_id, trace_id=trace_id
    )
    return MeasurementResult(**detail.model_dump(), trace_id=trace_id, idempotent=False)
