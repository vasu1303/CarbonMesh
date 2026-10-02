from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import EmissionFactor
from app.db.models.semantic import MetricDefinition
from app.modules.measurement.domain import (
    CONFIDENCE_V2_WEIGHTS,
    DEFAULT_CONFIDENCE_WEIGHTS,
    AmbiguousFactorError,
    FactorCandidate,
    InvalidConfidenceConfigurationError,
    InvalidQuantityError,
    NoMatchingFactorError,
    UnsupportedMassUnitError,
    calculate_confidence,
    calculate_confidence_v2,
    calculate_emissions,
    calculate_variance,
    measurement_code_hash,
    normalize_mass_to_kg,
    resolve_factor,
    sha256_payload,
    weighted_average,
)
from app.modules.measurement.repository import (
    MAX_CALCULATION_ACTIVITY_ROWS,
    MAX_FACTOR_CANDIDATES,
    ActivitySource,
    CalculationPersistenceItem,
    MeasurementContext,
    MeasurementDetailBundle,
    MeasurementPersistencePlan,
    MeasurementRepository,
)
from app.modules.measurement.schemas import (
    BaselineComparison,
    CalculationRunReference,
    ConfidenceBreakdownResponse,
    ConfidenceV2Response,
    EmissionCalculationDetail,
    EmissionFactorReference,
    EvidenceReference,
    GridPointReference,
    MeasurementBreakdown,
    MeasurementBreakdownItem,
    MeasurementCalculateRequest,
    MeasurementDetail,
    MeasurementFactReference,
    MeasurementInput,
    MeasurementListResponse,
    MeasurementResult,
    MeasurementSummary,
)


@dataclass(frozen=True, slots=True)
class MeasurementServiceError(Exception):
    status_code: int
    code: str
    message: str
    terminal_state: Literal[
        "no_data",
        "needs_clarification",
        "unsupported",
        "validation_error",
        "failed_validation",
    ]
    trace_id: str
    retryable: bool = False
    field_details: Mapping[str, object] | None = None

    def __str__(self) -> str:
        return self.message


class MeasurementService:
    def __init__(
        self,
        session: AsyncSession,
        repository: MeasurementRepository | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or MeasurementRepository(session)

    async def calculate(self, request: MeasurementCalculateRequest) -> MeasurementResult:
        if request.output_metric_key == "emissions.scope2.location_based":
            from app.modules.measurement.scope2 import calculate_scope2

            return await calculate_scope2(self, request)
        trace_id = request.trace_id or f"measurement-{uuid4()}"
        context = await self._resolve_context(request, trace_id=trace_id)
        if request.actor_id is not None and not await self.repository.actor_exists(
            company_id=request.company_id,
            actor_id=request.actor_id,
        ):
            raise self._context_missing("actor_id", request.actor_id, trace_id)
        if request.agent_run_id is not None and not await self.repository.agent_run_exists(
            company_id=request.company_id,
            agent_run_id=request.agent_run_id,
        ):
            raise self._context_missing("agent_run_id", request.agent_run_id, trace_id)
        await self.repository.acquire_calculation_lock(
            company_id=request.company_id,
            site_id=request.site_id,
            reporting_period_id=request.reporting_period_id,
            metric_definition_id=context.output_metric.id,
            material_code=request.material_code,
        )
        activities = await self.repository.list_activities(
            company_id=request.company_id,
            site_id=request.site_id,
            reporting_period_id=request.reporting_period_id,
            metric_definition_id=context.activity_metric.id,
            material_code=request.material_code,
            activity_record_ids=request.activity_record_ids,
        )
        if not activities:
            raise self._error(
                status_code=404,
                code="no_activity_data",
                message="No valid activity records match the frozen measurement context.",
                terminal_state="no_data",
                trace_id=trace_id,
                field_details={"material_code": request.material_code},
            )
        if len(activities) > MAX_CALCULATION_ACTIVITY_ROWS:
            raise self._error(
                status_code=422,
                code="activity_row_limit_exceeded",
                message=f"A measurement can include at most {MAX_CALCULATION_ACTIVITY_ROWS} rows.",
                terminal_state="validation_error",
                trace_id=trace_id,
            )
        if request.activity_record_ids is not None:
            returned_ids = {source.activity.id for source in activities}
            missing_ids = sorted(set(request.activity_record_ids) - returned_ids, key=str)
            if missing_ids:
                raise self._error(
                    status_code=422,
                    code="activity_scope_mismatch",
                    message="One or more requested activity records are outside the frozen context.",
                    terminal_state="failed_validation",
                    trace_id=trace_id,
                    field_details={"activity_record_ids": [str(item) for item in missing_ids]},
                )

        factors = await self.repository.list_factor_candidates(
            company_id=request.company_id,
            metric_definition_id=context.output_metric.id,
        )
        if len(factors) > MAX_FACTOR_CANDIDATES:
            raise self._error(
                status_code=409,
                code="factor_candidate_limit_exceeded",
                message="Too many active factors match the output metric; narrow or curate the factor set.",
                terminal_state="needs_clarification",
                trace_id=trace_id,
            )
        evidence_checksums = await self.repository.get_evidence_checksums(
            company_id=request.company_id,
            evidence_item_ids={factor.evidence_item_id for factor in factors},
        )
        missing_evidence_ids = sorted(
            {factor.evidence_item_id for factor in factors} - set(evidence_checksums),
            key=str,
        )
        if missing_evidence_ids:
            raise self._error(
                status_code=500,
                code="factor_evidence_missing",
                message="One or more active emission factors do not have accessible evidence.",
                terminal_state="failed_validation",
                trace_id=trace_id,
                field_details={"evidence_item_ids": [str(item) for item in missing_evidence_ids]},
            )

        rounding_policy, output_quantum, confidence_weights = self._method_configuration(
            context,
            trace_id=trace_id,
        )
        geography = request.geography or context.site.country_code
        candidates = [self._factor_candidate(factor) for factor in factors]
        items: list[CalculationPersistenceItem] = []
        for source in activities:
            activity = source.activity
            try:
                normalized_quantity = normalize_mass_to_kg(
                    activity.quantity,
                    activity.unit,
                    rounding=rounding_policy,
                )
                stored_normalized_quantity = normalize_mass_to_kg(
                    activity.normalized_quantity,
                    activity.normalized_unit,
                    rounding=rounding_policy,
                )
            except UnsupportedMassUnitError as error:
                raise self._error(
                    status_code=422,
                    code="unsupported_mass_unit",
                    message="An activity record uses a mass unit that cannot be normalized to kg.",
                    terminal_state="unsupported",
                    trace_id=trace_id,
                    field_details={
                        "activity_record_id": str(activity.id),
                        "unit": error.unit,
                    },
                ) from error
            except InvalidQuantityError as error:
                raise self._error(
                    status_code=422,
                    code="activity_quantity_out_of_range",
                    message="An activity quantity cannot be represented by the measurement model.",
                    terminal_state="failed_validation",
                    trace_id=trace_id,
                    field_details={"activity_record_id": str(activity.id)},
                ) from error
            if normalized_quantity != stored_normalized_quantity:
                raise self._error(
                    status_code=422,
                    code="normalized_quantity_mismatch",
                    message="Stored normalized activity does not match deterministic unit conversion.",
                    terminal_state="failed_validation",
                    trace_id=trace_id,
                    field_details={
                        "activity_record_id": str(activity.id),
                        "calculated_kg": format(normalized_quantity, "f"),
                        "stored_kg": format(stored_normalized_quantity, "f"),
                    },
                )

            try:
                resolved_factor = resolve_factor(
                    candidates,
                    material_code=activity.material_code,
                    product_code=source.product_code,
                    geography=geography,
                    effective_on=activity.activity_date or context.reporting_period.end_date,
                )
            except NoMatchingFactorError as error:
                raise self._error(
                    status_code=422,
                    code="emission_factor_not_found",
                    message="No valid emission factor matches an activity record and frozen context.",
                    terminal_state="unsupported",
                    trace_id=trace_id,
                    field_details={
                        "activity_record_id": str(activity.id),
                        "material_code": activity.material_code,
                        "product_code": source.product_code,
                        "geography": geography,
                        "effective_on": str(
                            activity.activity_date or context.reporting_period.end_date
                        ),
                    },
                ) from error
            except AmbiguousFactorError as error:
                raise self._error(
                    status_code=409,
                    code="emission_factor_ambiguous",
                    message="Multiple equally specific emission factors match an activity record.",
                    terminal_state="needs_clarification",
                    trace_id=trace_id,
                    field_details={
                        "activity_record_id": str(activity.id),
                        "factor_ids": [str(item) for item in error.factor_ids],
                    },
                ) from error
            except InvalidQuantityError as error:
                raise self._error(
                    status_code=422,
                    code="emission_factor_out_of_range",
                    message="A matching emission factor cannot be represented in canonical units.",
                    terminal_state="failed_validation",
                    trace_id=trace_id,
                    field_details={"activity_record_id": str(activity.id)},
                ) from error

            try:
                emissions = calculate_emissions(
                    normalized_quantity,
                    resolved_factor.factor_kgco2e_per_kg,
                    rounding=rounding_policy,
                    quantum=output_quantum,
                )
            except InvalidQuantityError as error:
                raise self._error(
                    status_code=422,
                    code="measurement_value_out_of_range",
                    message="Calculated emissions exceed the supported numeric range.",
                    terminal_state="failed_validation",
                    trace_id=trace_id,
                    field_details={"activity_record_id": str(activity.id)},
                ) from error
            formula = (
                f"{format(normalized_quantity, 'f')} kg * "
                f"{format(resolved_factor.factor_kgco2e_per_kg, 'f')} kgCO2e/kg = "
                f"{format(emissions, 'f')} kgCO2e"
            )
            calculation_hash = sha256_payload(
                {
                    "activity_record_id": activity.id,
                    "emission_factor_id": resolved_factor.candidate.id,
                    "normalized_quantity_kg": normalized_quantity,
                    "factor_kgco2e_per_kg": resolved_factor.factor_kgco2e_per_kg,
                    "emissions_kgco2e": emissions,
                    "formula": formula,
                }
            )
            items.append(
                CalculationPersistenceItem(
                    source=source,
                    factor=self._factor_model(factors, resolved_factor.candidate.id),
                    normalized_quantity_kg=normalized_quantity,
                    factor_kgco2e_per_kg=resolved_factor.factor_kgco2e_per_kg,
                    emissions_kgco2e=emissions,
                    record_completeness=self._record_completeness(source),
                    formula=formula,
                    output_hash=calculation_hash,
                )
            )

        try:
            total = calculate_emissions(
                sum((item.emissions_kgco2e for item in items), start=Decimal(0)),
                Decimal(1),
                rounding=rounding_policy,
                quantum=output_quantum,
            )
        except InvalidQuantityError as error:
            raise self._error(
                status_code=422,
                code="measurement_value_out_of_range",
                message="Total emissions exceed the supported numeric range.",
                terminal_state="failed_validation",
                trace_id=trace_id,
            ) from error
        source_quality = weighted_average(
            ((item.factor.source_quality, item.normalized_quantity_kg) for item in items),
            rounding=rounding_policy,
        )
        factor_specificity = weighted_average(
            ((item.factor.factor_specificity, item.normalized_quantity_kg) for item in items),
            rounding=rounding_policy,
        )
        factor_recency = weighted_average(
            ((item.factor.factor_recency, item.normalized_quantity_kg) for item in items),
            rounding=rounding_policy,
        )
        record_completeness = weighted_average(
            ((item.record_completeness, item.normalized_quantity_kg) for item in items),
            rounding=rounding_policy,
        )
        try:
            if set(confidence_weights) == set(CONFIDENCE_V2_WEIGHTS):
                confidence = calculate_confidence_v2(
                    source_quality=source_quality,
                    method_fit=factor_specificity,
                    temporal_match=factor_recency,
                    completeness=record_completeness,
                )
            else:
                confidence = calculate_confidence(
                    source_quality=source_quality,
                    factor_specificity=factor_specificity,
                    factor_recency=factor_recency,
                    record_completeness=record_completeness,
                    weights=confidence_weights,
                    rounding=rounding_policy,
                )
        except InvalidConfidenceConfigurationError as error:
            raise self._error(
                status_code=500,
                code="invalid_measurement_method",
                message="The configured measurement confidence method is invalid.",
                terminal_state="failed_validation",
                trace_id=trace_id,
            ) from error

        baseline = await self.repository.get_baseline(
            company_id=request.company_id,
            site_id=request.site_id,
            reporting_period_id=request.reporting_period_id,
            metric_definition_id=context.output_metric.id,
        )
        input_payload = {
            "company_id": request.company_id,
            "site_id": request.site_id,
            "reporting_period_id": request.reporting_period_id,
            "activity_metric_definition_id": context.activity_metric.id,
            "activity_metric_key": context.activity_metric.key,
            "activity_metric_version": context.activity_metric.version,
            "activity_metric_canonical_unit": context.activity_metric.canonical_unit,
            "output_metric_definition_id": context.output_metric.id,
            "output_metric_key": context.output_metric.key,
            "output_metric_version": context.output_metric.version,
            "output_metric_canonical_unit": context.output_metric.canonical_unit,
            "method_definition_id": context.method.id,
            "method_key": context.method.key,
            "method_version": context.method.version,
            "method_code_version": context.method.code_version,
            "code_hash": measurement_code_hash(),
            "method_configuration": context.method.configuration,
            "rounding_policy": "ROUND_HALF_EVEN",
            "output_quantum": output_quantum,
            "confidence_weights": confidence_weights,
            "material_code": request.material_code,
            "geography": geography,
            "activities": [
                {
                    "id": item.source.activity.id,
                    "raw_activity_record_id": item.source.activity.raw_activity_record_id,
                    "raw_activity_data_source_id": item.source.raw_activity.data_source_id,
                    "raw_activity_source_document_id": (
                        item.source.raw_activity.source_document_id
                    ),
                    "raw_activity_row_key": item.source.raw_activity.row_key,
                    "raw_activity_row_number": item.source.raw_activity.row_number,
                    "raw_activity_checksum": item.source.raw_activity.checksum,
                    "supplier_product_id": item.source.activity.supplier_product_id,
                    "product_code": item.source.product_code,
                    "material_code": item.source.activity.material_code,
                    "activity_date": item.source.activity.activity_date,
                    "source_quantity": item.source.activity.quantity,
                    "source_unit": item.source.activity.unit,
                    "stored_normalized_quantity": item.source.activity.normalized_quantity,
                    "stored_normalized_unit": item.source.activity.normalized_unit,
                    "unit_cost": item.source.activity.unit_cost,
                    "currency": item.source.activity.currency,
                    "normalized_quantity_kg": item.normalized_quantity_kg,
                    "record_completeness": item.record_completeness,
                    "factor_id": item.factor.id,
                    "factor_code": item.factor.factor_code,
                    "factor_name": item.factor.name,
                    "factor_version": item.factor.version,
                    "factor_material_code": item.factor.material_code,
                    "factor_product_code": item.factor.product_code,
                    "factor_geography": item.factor.geography,
                    "factor_value": item.factor.factor_value,
                    "factor_numerator_unit": item.factor.numerator_unit,
                    "factor_denominator_unit": item.factor.denominator_unit,
                    "factor_effective_from": item.factor.effective_from,
                    "factor_effective_to": item.factor.effective_to,
                    "factor_source_quality": item.factor.source_quality,
                    "factor_specificity": item.factor.factor_specificity,
                    "factor_recency": item.factor.factor_recency,
                    "factor_evidence_item_id": item.factor.evidence_item_id,
                    "factor_evidence_checksum": evidence_checksums.get(
                        item.factor.evidence_item_id
                    ),
                    "factor_kgco2e_per_kg": item.factor_kgco2e_per_kg,
                    "emissions_kgco2e": item.emissions_kgco2e,
                    "calculation_output_hash": item.output_hash,
                }
                for item in items
            ],
            "baseline": (
                {
                    "id": baseline.id,
                    "name": baseline.name,
                    "value_kgco2e": baseline.value_kgco2e,
                    "unit": baseline.unit,
                    "frozen_hash": baseline.frozen_hash,
                }
                if baseline is not None
                else None
            ),
        }
        input_hash = sha256_payload(input_payload)
        existing_id = await self.repository.find_measurement_id_by_input_hash(
            company_id=request.company_id,
            input_hash=input_hash,
        )
        if existing_id is not None:
            detail = await self.get_measurement(
                company_id=request.company_id,
                measurement_id=existing_id,
                trace_id=trace_id,
            )
            return MeasurementResult(
                **detail.model_dump(),
                trace_id=trace_id,
                idempotent=True,
            )

        output_hash = sha256_payload(
            {
                "input_hash": input_hash,
                "value_kgco2e": total,
                "unit": "kgCO2e",
                "confidence": confidence.overall,
                "calculation_hashes": [item.output_hash for item in items],
            }
        )
        variance = (
            calculate_variance(total, baseline.value_kgco2e, rounding=rounding_policy)
            if baseline is not None
            else None
        )
        supersedes_measurement = await self.repository.get_latest_verified_measurement(
            company_id=request.company_id,
            site_id=request.site_id,
            reporting_period_id=request.reporting_period_id,
            metric_definition_id=context.output_metric.id,
            material_code=request.material_code,
        )
        formula = "sum(normalized_quantity_kg * factor_kgco2e_per_kg)"
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
            formula=formula,
            value_kgco2e=total,
            confidence=confidence,
            items=tuple(items),
            baseline=baseline,
            variance=variance,
            supersedes_measurement=supersedes_measurement,
        )
        try:
            persisted = await self.repository.persist_measurement(plan)
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            existing_id = await self.repository.find_measurement_id_by_input_hash(
                company_id=request.company_id,
                input_hash=input_hash,
            )
            if existing_id is None:
                raise self._error(
                    status_code=409,
                    code="measurement_persistence_conflict",
                    message="The measurement conflicted with an existing immutable result.",
                    terminal_state="failed_validation",
                    trace_id=trace_id,
                ) from error
            detail = await self.get_measurement(
                company_id=request.company_id,
                measurement_id=existing_id,
                trace_id=trace_id,
            )
            return MeasurementResult(
                **detail.model_dump(),
                trace_id=trace_id,
                idempotent=True,
            )
        except BaseException:
            await self.session.rollback()
            raise

        detail = await self.get_measurement(
            company_id=request.company_id,
            measurement_id=persisted.measurement_id,
            trace_id=trace_id,
        )
        return MeasurementResult(
            **detail.model_dump(),
            trace_id=trace_id,
            idempotent=False,
        )

    async def list_measurements(
        self,
        *,
        company_id: UUID,
        site_id: UUID | None,
        reporting_period_id: UUID | None,
        category: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> MeasurementListResponse:
        rows, total = await self.repository.list_measurements(
            company_id=company_id,
            site_id=site_id,
            reporting_period_id=reporting_period_id,
            category=category,
            status=status,
            limit=limit,
            offset=offset,
        )
        items = [
            MeasurementSummary(
                id=measurement.id,
                company_id=measurement.company_id,
                site_id=measurement.site_id,
                site_name=site.name,
                reporting_period_id=measurement.reporting_period_id,
                reporting_period_name=period.name,
                metric_definition_id=measurement.metric_definition_id,
                metric_key=metric.key,
                metric_version=metric.version,
                category=metric.key,
                value_kgco2e=measurement.value_kgco2e,
                unit=measurement.unit,
                confidence=measurement.confidence,
                status=measurement.status,
                ledger_event_id=measurement.ledger_event_id,
                output_hash=measurement.output_hash,
                verified_at=measurement.verified_at,
                created_at=measurement.created_at,
            )
            for measurement, metric, site, period in rows
        ]
        return MeasurementListResponse(items=items, total=total, limit=limit, offset=offset)

    async def get_measurement(
        self,
        *,
        company_id: UUID,
        measurement_id: UUID,
        trace_id: str | None = None,
    ) -> MeasurementDetail:
        resolved_trace_id = trace_id or f"measurement-{uuid4()}"
        bundle = await self.repository.get_detail_bundle(
            company_id=company_id,
            measurement_id=measurement_id,
        )
        if bundle is None:
            raise self._error(
                status_code=404,
                code="measurement_not_found",
                message="The requested measurement was not found for this company.",
                terminal_state="no_data",
                trace_id=resolved_trace_id,
                field_details={"measurement_id": str(measurement_id)},
            )
        return self._detail_from_bundle(bundle)

    async def get_breakdown(
        self, *, company_id: UUID, measurement_id: UUID
    ) -> MeasurementBreakdown:
        detail = await self.get_measurement(company_id=company_id, measurement_id=measurement_id)
        inputs = {item.activity_record_id: item for item in detail.inputs}
        items = []
        for calculation in detail.calculations:
            source = inputs[calculation.activity_record_id]
            is_grid = calculation.grid_intensity_point_id is not None
            items.append(
                MeasurementBreakdownItem(
                    calculation_id=calculation.id,
                    activity_record_id=source.activity_record_id,
                    raw_activity_record_id=source.raw_activity_record_id,
                    source_document_id=source.source_document_id,
                    interval_start=calculation.interval_start,
                    activity_date=source.activity_date,
                    material_code=source.material_code,
                    quantity=(
                        calculation.normalized_quantity_kwh
                        if is_grid
                        else calculation.normalized_quantity_kg
                    ),
                    quantity_unit="kWh" if is_grid else "kg",
                    emissions_kgco2e=calculation.emissions_kgco2e,
                    emission_factor_id=calculation.emission_factor_id,
                    grid_intensity_point_id=calculation.grid_intensity_point_id,
                    output_hash=calculation.output_hash,
                )
            )
        return MeasurementBreakdown(
            measurement_id=detail.id,
            metric_key=detail.metric_key,
            status=detail.status,
            total_kgco2e=detail.value_kgco2e,
            facts=detail.facts,
            items=items,
        )

    async def _resolve_context(
        self,
        request: MeasurementCalculateRequest,
        *,
        trace_id: str,
    ) -> MeasurementContext:
        if not await self.repository.company_exists(company_id=request.company_id):
            raise self._context_missing("company_id", request.company_id, trace_id)
        site = await self.repository.get_site(
            company_id=request.company_id,
            site_id=request.site_id,
        )
        if site is None:
            raise self._context_missing("site_id", request.site_id, trace_id)
        period = await self.repository.get_reporting_period(
            company_id=request.company_id,
            reporting_period_id=request.reporting_period_id,
        )
        if period is None:
            raise self._context_missing(
                "reporting_period_id",
                request.reporting_period_id,
                trace_id,
            )
        activity_metrics = await self.repository.list_active_metrics(
            company_id=request.company_id,
            key=request.activity_metric_key,
        )
        activity_metric = self._one_versioned_definition(
            activity_metrics,
            kind="activity metric",
            key=request.activity_metric_key,
            trace_id=trace_id,
        )
        output_metrics = await self.repository.list_active_metrics(
            company_id=request.company_id,
            key=request.output_metric_key,
        )
        output_metric = self._one_versioned_definition(
            output_metrics,
            kind="output metric",
            key=request.output_metric_key,
            trace_id=trace_id,
        )
        allowed_activity_units = (
            {"kwh"}
            if request.output_metric_key == "emissions.scope2.location_based"
            else {
                "kg",
                "kilogram",
                "kilograms",
            }
        )
        if activity_metric.canonical_unit.strip().casefold() not in allowed_activity_units:
            raise self._error(
                status_code=422,
                code="unsupported_activity_metric_unit",
                message="The activity metric unit is incompatible with the selected calculation.",
                terminal_state="unsupported",
                trace_id=trace_id,
                field_details={"canonical_unit": activity_metric.canonical_unit},
            )
        if output_metric.canonical_unit.strip().casefold() != "kgco2e":
            raise self._error(
                status_code=422,
                code="unsupported_output_metric_unit",
                message="The selected output metric does not use canonical kgCO2e.",
                terminal_state="unsupported",
                trace_id=trace_id,
                field_details={"canonical_unit": output_metric.canonical_unit},
            )
        methods = await self.repository.list_active_methods(
            company_id=request.company_id,
            key=request.method_key,
            version=output_metric.method_version,
            effective_on=period.end_date,
        )
        if not methods:
            raise self._error(
                status_code=404,
                code="measurement_method_not_found",
                message="No active measurement method matches the output metric version and period.",
                terminal_state="no_data",
                trace_id=trace_id,
                field_details={
                    "method_key": request.method_key,
                    "method_version": output_metric.method_version,
                },
            )
        if len(methods) != 1:
            raise self._error(
                status_code=409,
                code="measurement_method_ambiguous",
                message="Multiple measurement methods match the frozen context.",
                terminal_state="needs_clarification",
                trace_id=trace_id,
                field_details={"method_key": request.method_key},
            )
        return MeasurementContext(
            site=site,
            reporting_period=period,
            activity_metric=activity_metric,
            output_metric=output_metric,
            method=methods[0],
        )

    def _one_versioned_definition(
        self,
        definitions: list[MetricDefinition],
        *,
        kind: str,
        key: str,
        trace_id: str,
    ) -> MetricDefinition:
        if not definitions:
            raise self._error(
                status_code=404,
                code="metric_definition_not_found",
                message=f"The requested {kind} is not configured.",
                terminal_state="no_data",
                trace_id=trace_id,
                field_details={"metric_key": key},
            )
        if len(definitions) != 1:
            raise self._error(
                status_code=409,
                code="metric_definition_ambiguous",
                message=f"Multiple active versions of the requested {kind} are configured.",
                terminal_state="needs_clarification",
                trace_id=trace_id,
                field_details={"metric_key": key},
            )
        return definitions[0]

    def _method_configuration(
        self,
        context: MeasurementContext,
        *,
        trace_id: str,
    ) -> tuple[str, Decimal, dict[str, Decimal]]:
        raw_configuration = context.method.configuration
        if raw_configuration is None:
            configuration: Mapping[str, Any] = {}
        elif isinstance(raw_configuration, Mapping):
            configuration = raw_configuration
        else:
            raise self._error(
                status_code=500,
                code="invalid_measurement_method",
                message="The measurement method configuration is invalid.",
                terminal_state="failed_validation",
                trace_id=trace_id,
            )
        rounding_name = str(configuration.get("rounding_policy", "ROUND_HALF_EVEN"))
        if rounding_name != "ROUND_HALF_EVEN":
            raise self._error(
                status_code=500,
                code="unsupported_rounding_policy",
                message="The measurement method configures an unsupported rounding policy.",
                terminal_state="failed_validation",
                trace_id=trace_id,
            )
        try:
            scale = int(configuration.get("output_scale", 6))
            if scale < 0 or scale > 6:
                raise ValueError
            quantum = Decimal(1).scaleb(-scale)
            configured_weights = configuration.get("confidence_weights", {})
            if not isinstance(configured_weights, Mapping):
                raise TypeError
            uses_v2 = configuration.get("confidence_method") == "measurement-confidence-v2"
            if uses_v2 and context.method.version != "2.0.0":
                raise ValueError
            defaults = CONFIDENCE_V2_WEIGHTS if uses_v2 else DEFAULT_CONFIDENCE_WEIGHTS
            if any(
                not isinstance(name, str) or name not in defaults for name in configured_weights
            ):
                raise ValueError
            weights = {
                name: Decimal(str(configured_weights.get(name, default)))
                for name, default in defaults.items()
            }
            if any(not weight.is_finite() for weight in weights.values()):
                raise ValueError
            if uses_v2 and weights != CONFIDENCE_V2_WEIGHTS:
                raise ValueError
        except (InvalidOperation, TypeError, ValueError) as error:
            raise self._error(
                status_code=500,
                code="invalid_measurement_method",
                message="The measurement method configuration is invalid.",
                terminal_state="failed_validation",
                trace_id=trace_id,
            ) from error
        return ROUND_HALF_EVEN, quantum, weights

    @staticmethod
    def _factor_candidate(factor: EmissionFactor) -> FactorCandidate:
        return FactorCandidate(
            id=factor.id,
            factor_code=factor.factor_code,
            version=factor.version,
            material_code=factor.material_code,
            product_code=factor.product_code,
            geography=factor.geography,
            factor_value=factor.factor_value,
            numerator_unit=factor.numerator_unit,
            denominator_unit=factor.denominator_unit,
            effective_from=factor.effective_from,
            effective_to=factor.effective_to,
            source_quality=factor.source_quality,
            factor_specificity=factor.factor_specificity,
            factor_recency=factor.factor_recency,
            evidence_item_id=factor.evidence_item_id,
        )

    @staticmethod
    def _factor_model(factors: list[EmissionFactor], factor_id: UUID) -> EmissionFactor:
        return next(factor for factor in factors if factor.id == factor_id)

    @staticmethod
    def _record_completeness(source: ActivitySource) -> Decimal:
        activity = source.activity
        fields = (
            activity.raw_activity_record_id is not None,
            bool(activity.material_code.strip()),
            activity.quantity is not None,
            bool(activity.unit.strip()),
            activity.activity_date is not None,
            activity.supplier_product_id is not None and source.product_code is not None,
            activity.unit_cost is not None,
            activity.currency is not None,
        )
        return (Decimal(sum(fields)) / Decimal(len(fields))).quantize(Decimal("0.00001"))

    def _detail_from_bundle(self, bundle: MeasurementDetailBundle) -> MeasurementDetail:
        measurement = bundle.measurement
        run = bundle.calculation_run
        confidence_data = run.summary.get("confidence", {}) if run.summary else {}
        weights_data = confidence_data.get("weights", {})

        def decimal_value(container: Mapping[str, Any], key: str, default: Decimal) -> Decimal:
            try:
                return Decimal(str(container.get(key, default)))
            except InvalidOperation:
                return default

        overall = measurement.confidence
        weights = {
            name: decimal_value(weights_data, name, default)
            for name, default in DEFAULT_CONFIDENCE_WEIGHTS.items()
        }
        confidence = ConfidenceBreakdownResponse(
            source_quality=decimal_value(confidence_data, "source_quality", overall),
            factor_specificity=decimal_value(confidence_data, "factor_specificity", overall),
            factor_recency=decimal_value(confidence_data, "factor_recency", overall),
            record_completeness=decimal_value(confidence_data, "record_completeness", overall),
            source_quality_weight=weights["source_quality"],
            factor_specificity_weight=weights["factor_specificity"],
            factor_recency_weight=weights["factor_recency"],
            record_completeness_weight=weights["record_completeness"],
            overall=overall,
        )
        if confidence_data.get("version") == "2.0.0":
            confidence = ConfidenceV2Response.model_validate(confidence_data)

        inputs: list[MeasurementInput] = []
        calculations: list[EmissionCalculationDetail] = []
        factors: dict[UUID, EmissionFactorReference] = {}
        for (
            calculation,
            activity,
            product_code,
            raw_activity,
            factor,
            evidence,
            source_document,
        ) in bundle.calculations:
            completeness = self._record_completeness(
                ActivitySource(
                    activity=activity,
                    product_code=product_code,
                    raw_activity=raw_activity,
                )
            )
            inputs.append(
                MeasurementInput(
                    activity_record_id=activity.id,
                    raw_activity_record_id=activity.raw_activity_record_id,
                    data_source_id=raw_activity.data_source_id,
                    source_document_id=raw_activity.source_document_id,
                    source_row_key=raw_activity.row_key,
                    source_row_number=raw_activity.row_number,
                    raw_checksum=raw_activity.checksum,
                    supplier_product_id=activity.supplier_product_id,
                    product_code=product_code,
                    material_code=activity.material_code,
                    activity_date=activity.activity_date,
                    source_quantity=activity.quantity,
                    source_unit=activity.unit,
                    normalized_quantity_kg=calculation.normalized_quantity,
                    record_completeness=completeness,
                )
            )
            calculations.append(
                EmissionCalculationDetail(
                    id=calculation.id,
                    activity_record_id=calculation.activity_record_id,
                    emission_factor_id=calculation.emission_factor_id,
                    normalized_quantity_kg=calculation.normalized_quantity,
                    factor_kgco2e_per_kg=calculation.factor_value,
                    emissions_kgco2e=calculation.emissions_kgco2e,
                    formula=calculation.formula,
                    output_hash=calculation.output_hash,
                )
            )
            factors.setdefault(
                factor.id,
                EmissionFactorReference(
                    id=factor.id,
                    factor_code=factor.factor_code,
                    version=factor.version,
                    name=factor.name,
                    material_code=factor.material_code,
                    product_code=factor.product_code,
                    geography=factor.geography,
                    factor_value=factor.factor_value,
                    numerator_unit=factor.numerator_unit,
                    denominator_unit=factor.denominator_unit,
                    normalized_factor_kgco2e_per_kg=calculation.factor_value,
                    effective_from=factor.effective_from,
                    effective_to=factor.effective_to,
                    evidence=EvidenceReference(
                        id=evidence.id,
                        source_document_id=evidence.source_document_id,
                        data_source_id=source_document.data_source_id,
                        source_document_filename=source_document.filename,
                        source_document_checksum=source_document.checksum,
                        evidence_type=evidence.evidence_type,
                        locator=evidence.locator,
                        checksum=evidence.checksum,
                    ),
                ),
            )

        grid_points = []
        for (
            calculation,
            activity,
            raw_activity,
            point,
            evidence,
            document,
        ) in bundle.grid_calculations:
            inputs.append(
                MeasurementInput(
                    activity_record_id=activity.id,
                    raw_activity_record_id=raw_activity.id,
                    data_source_id=raw_activity.data_source_id,
                    source_document_id=raw_activity.source_document_id,
                    source_row_key=raw_activity.row_key,
                    source_row_number=raw_activity.row_number,
                    raw_checksum=raw_activity.checksum,
                    supplier_product_id=None,
                    product_code=None,
                    material_code=activity.material_code,
                    activity_date=activity.activity_date,
                    source_quantity=activity.quantity,
                    source_unit=activity.unit,
                    normalized_quantity_kwh=calculation.normalized_quantity,
                    interval_start=point.observed_at,
                    record_completeness=Decimal(1),
                )
            )
            calculations.append(
                EmissionCalculationDetail(
                    id=calculation.id,
                    activity_record_id=activity.id,
                    emission_factor_id=None,
                    grid_intensity_point_id=point.id,
                    normalized_quantity_kwh=calculation.normalized_quantity,
                    intensity_gco2e_per_kwh=calculation.factor_value,
                    interval_start=point.observed_at,
                    emissions_kgco2e=calculation.emissions_kgco2e,
                    formula=calculation.formula,
                    output_hash=calculation.output_hash,
                )
            )
            grid_points.append(
                GridPointReference(
                    id=point.id,
                    provider=point.provider,
                    zone=point.zone,
                    observed_at=point.observed_at,
                    temporal_granularity=point.temporal_granularity,
                    intensity_gco2e_per_kwh=calculation.factor_value,
                    is_estimated=point.is_estimated,
                    method_version=point.method_version,
                    point_hash=point.point_hash,
                    evidence=EvidenceReference(
                        id=evidence.id,
                        source_document_id=evidence.source_document_id,
                        data_source_id=document.data_source_id,
                        source_document_filename=document.filename,
                        source_document_checksum=document.checksum,
                        evidence_type=evidence.evidence_type,
                        locator=evidence.locator,
                        checksum=evidence.checksum,
                    ),
                )
            )

        baseline_response = None
        summary = run.summary if isinstance(run.summary, Mapping) else {}
        baseline_was_frozen = "baseline" in summary
        summary_baseline = summary.get("baseline")
        if isinstance(summary_baseline, dict):
            baseline_value = Decimal(str(summary_baseline["value_kgco2e"]))
            frozen_variance = Decimal(str(run.summary["variance_kgco2e"]))
            frozen_variance_pct = run.summary.get("variance_pct")
            baseline_response = BaselineComparison(
                baseline_id=UUID(str(summary_baseline["id"])),
                name=str(summary_baseline["name"]),
                value_kgco2e=baseline_value,
                unit=str(summary_baseline["unit"]),
                variance_kgco2e=frozen_variance,
                variance_pct=(
                    Decimal(str(frozen_variance_pct)) if frozen_variance_pct is not None else None
                ),
                variance_alert_id=(
                    bundle.variance_alert.id if bundle.variance_alert is not None else None
                ),
            )
        elif not baseline_was_frozen and bundle.baseline is not None:
            if bundle.variance_alert is not None:
                variance_kgco2e = bundle.variance_alert.variance_kgco2e
                variance_pct = bundle.variance_alert.variance_pct
                variance_alert_id = bundle.variance_alert.id
            else:
                computed_variance = calculate_variance(
                    measurement.value_kgco2e,
                    bundle.baseline.value_kgco2e,
                )
                variance_kgco2e = computed_variance.variance_kgco2e
                variance_pct = computed_variance.variance_pct
                variance_alert_id = None
            baseline_response = BaselineComparison(
                baseline_id=bundle.baseline.id,
                name=bundle.baseline.name,
                value_kgco2e=bundle.baseline.value_kgco2e,
                unit=bundle.baseline.unit,
                variance_kgco2e=variance_kgco2e,
                variance_pct=variance_pct,
                variance_alert_id=variance_alert_id,
            )

        return MeasurementDetail(
            id=measurement.id,
            company_id=measurement.company_id,
            site_id=measurement.site_id,
            site_name=bundle.site.name,
            reporting_period_id=measurement.reporting_period_id,
            reporting_period_name=bundle.reporting_period.name,
            metric_definition_id=measurement.metric_definition_id,
            metric_key=bundle.metric.key,
            metric_version=bundle.metric.version,
            category=bundle.metric.key,
            value_kgco2e=measurement.value_kgco2e,
            unit=measurement.unit,
            confidence=measurement.confidence,
            confidence_breakdown=confidence,
            status=measurement.status,
            formula=measurement.formula,
            output_hash=measurement.output_hash,
            verified_at=measurement.verified_at,
            created_at=measurement.created_at,
            calculation_run=CalculationRunReference(
                id=run.id,
                method_definition_id=run.method_definition_id,
                method_key=bundle.method.key,
                method_version=run.method_version,
                code_version=run.code_version,
                method_hash=run.summary.get("method_hash"),
                code_hash=run.summary.get("code_hash"),
                rounding_policy=run.rounding_policy,
                input_hash=run.input_hash,
                output_hash=run.output_hash or measurement.output_hash,
                status=run.status,
                started_at=run.started_at,
                completed_at=run.completed_at,
            ),
            inputs=inputs,
            factors=list(factors.values()),
            grid_points=grid_points,
            coverage=(run.summary.get("input_snapshot") or {}).get("coverage"),
            calculations=calculations,
            baseline=baseline_response,
            facts=MeasurementFactReference(
                fact_id=measurement.id,
                ledger_event_id=measurement.ledger_event_id,
                output_hash=measurement.output_hash,
                audit_log_id=bundle.audit_log_id,
            ),
        )

    def _context_missing(self, field: str, value: object, trace_id: str) -> MeasurementServiceError:
        return self._error(
            status_code=404,
            code="measurement_context_not_found",
            message="The requested measurement context was not found for this company.",
            terminal_state="no_data",
            trace_id=trace_id,
            field_details={field: str(value)},
        )

    @staticmethod
    def _error(
        *,
        status_code: int,
        code: str,
        message: str,
        terminal_state: Literal[
            "no_data",
            "needs_clarification",
            "unsupported",
            "validation_error",
            "failed_validation",
        ],
        trace_id: str,
        retryable: bool = False,
        field_details: Mapping[str, object] | None = None,
    ) -> MeasurementServiceError:
        return MeasurementServiceError(
            status_code=status_code,
            code=code,
            message=message,
            terminal_state=terminal_state,
            trace_id=trace_id,
            retryable=retryable,
            field_details=field_details,
        )
