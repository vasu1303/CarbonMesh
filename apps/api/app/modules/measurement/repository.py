from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import (
    ActivityRecord,
    AgentRun,
    CalculationRun,
    CarbonBaseline,
    CarbonMeasurement,
    DataQualityIssue,
    EmissionCalculation,
    EmissionFactor,
    GridIntensityPoint,
    RawActivityRecord,
    VarianceAlert,
)
from app.db.models.core import (
    Actor,
    AuditLog,
    Company,
    EvidenceItem,
    ReportingPeriod,
    Site,
    SourceDocument,
)
from app.db.models.ledger import LedgerEvent, LedgerEventEvidence, LineageEdge
from app.db.models.procurement import SupplierProduct
from app.db.models.semantic import MethodDefinition, MetricDefinition
from app.modules.measurement.domain import (
    ConfidenceBreakdown,
    ConfidenceV2,
    VarianceResult,
    canonical_json_value,
    measurement_code_hash,
    sha256_payload,
)

MAX_CALCULATION_ACTIVITY_ROWS = 3000
MAX_FACTOR_CANDIDATES = 100


@dataclass(frozen=True, slots=True)
class MeasurementContext:
    site: Site
    reporting_period: ReportingPeriod
    activity_metric: MetricDefinition
    output_metric: MetricDefinition
    method: MethodDefinition


@dataclass(frozen=True, slots=True)
class ActivitySource:
    activity: ActivityRecord
    product_code: str | None
    raw_activity: RawActivityRecord


@dataclass(frozen=True, slots=True)
class CalculationPersistenceItem:
    source: ActivitySource
    factor: EmissionFactor
    normalized_quantity_kg: Decimal
    factor_kgco2e_per_kg: Decimal
    emissions_kgco2e: Decimal
    record_completeness: Decimal
    formula: str
    output_hash: str


@dataclass(frozen=True, slots=True)
class GridCalculationPersistenceItem:
    source: ActivitySource
    grid_point: GridIntensityPoint
    interval_start: datetime
    normalized_quantity_kwh: Decimal
    emissions_kgco2e: Decimal
    record_completeness: Decimal
    formula: str
    output_hash: str


@dataclass(frozen=True, slots=True)
class MeasurementPersistencePlan:
    company_id: UUID
    actor_id: UUID | None
    agent_run_id: UUID | None
    trace_id: str
    material_code: str
    context: MeasurementContext
    input_hash: str
    output_hash: str
    rounding_policy: str
    formula: str
    value_kgco2e: Decimal
    confidence: ConfidenceBreakdown | ConfidenceV2
    items: tuple[CalculationPersistenceItem | GridCalculationPersistenceItem, ...]
    baseline: CarbonBaseline | None
    variance: VarianceResult | None
    supersedes_measurement: CarbonMeasurement | None
    input_snapshot: dict | None = None


@dataclass(frozen=True, slots=True)
class PersistedMeasurement:
    measurement_id: UUID
    calculation_run_id: UUID
    ledger_event_id: UUID
    variance_alert_id: UUID | None
    audit_log_id: UUID


@dataclass(frozen=True, slots=True)
class MeasurementDetailBundle:
    measurement: CarbonMeasurement
    calculation_run: CalculationRun
    metric: MetricDefinition
    method: MethodDefinition
    site: Site
    reporting_period: ReportingPeriod
    audit_log_id: UUID | None
    calculations: tuple[
        tuple[
            EmissionCalculation,
            ActivityRecord,
            str | None,
            RawActivityRecord,
            EmissionFactor,
            EvidenceItem,
            SourceDocument,
        ],
        ...,
    ]
    baseline: CarbonBaseline | None
    variance_alert: VarianceAlert | None
    grid_calculations: tuple = ()


class MeasurementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_grid_points(
        self,
        *,
        company_id: UUID,
        site_id: UUID,
        zone: str,
        start: datetime,
        end: datetime,
        method_version: str | None,
    ) -> list[GridIntensityPoint]:
        statement = (
            select(GridIntensityPoint)
            .join(
                MetricDefinition,
                and_(
                    MetricDefinition.company_id == GridIntensityPoint.company_id,
                    MetricDefinition.id == GridIntensityPoint.metric_definition_id,
                ),
            )
            .where(
                GridIntensityPoint.company_id == company_id,
                GridIntensityPoint.site_id == site_id,
                GridIntensityPoint.zone == zone,
                GridIntensityPoint.observed_at >= start,
                GridIntensityPoint.observed_at <= end,
                GridIntensityPoint.temporal_granularity == "hourly",
                MetricDefinition.key == "electricity.grid_carbon_intensity",
                MetricDefinition.canonical_unit == "gCO2e/kWh",
            )
            .order_by(GridIntensityPoint.observed_at, GridIntensityPoint.id)
        )
        if method_version is not None:
            statement = statement.where(GridIntensityPoint.method_version == method_version)
        return list(
            (
                await self.session.scalars(statement.limit(MAX_CALCULATION_ACTIVITY_ROWS * 2 + 1))
            ).all()
        )

    async def has_blocking_hourly_issues(
        self,
        *,
        company_id: UUID,
        source_document_ids: set[UUID],
        data_source_ids: set[UUID],
    ) -> bool:
        return (
            await self.session.scalar(
                select(DataQualityIssue.id)
                .outerjoin(
                    RawActivityRecord,
                    and_(
                        RawActivityRecord.company_id == DataQualityIssue.company_id,
                        RawActivityRecord.id == DataQualityIssue.raw_activity_record_id,
                    ),
                )
                .where(
                    DataQualityIssue.company_id == company_id,
                    (
                        RawActivityRecord.source_document_id.in_(source_document_ids)
                        | DataQualityIssue.details["import_id"]
                        .as_string()
                        .in_([str(identifier) for identifier in data_source_ids])
                    ),
                    DataQualityIssue.severity == "error",
                    DataQualityIssue.details["blocks_verification"].as_boolean().is_(True),
                )
                .limit(1)
            )
            is not None
        )

    async def get_grid_evidence(self, *, company_id: UUID, evidence_item_ids: set[UUID]):
        return {
            item.id: (item, document)
            for item, document in (
                await self.session.execute(
                    select(EvidenceItem, SourceDocument)
                    .join(
                        SourceDocument,
                        and_(
                            SourceDocument.company_id == EvidenceItem.company_id,
                            SourceDocument.id == EvidenceItem.source_document_id,
                        ),
                    )
                    .where(
                        EvidenceItem.company_id == company_id,
                        EvidenceItem.id.in_(evidence_item_ids),
                    )
                )
            ).all()
        }

    async def company_exists(self, *, company_id: UUID) -> bool:
        identifier = await self.session.scalar(
            select(Company.id).where(
                Company.id == company_id,
                Company.is_active.is_(True),
            )
        )
        return identifier is not None

    async def get_site(self, *, company_id: UUID, site_id: UUID) -> Site | None:
        return await self.session.scalar(
            select(Site).where(
                Site.company_id == company_id,
                Site.id == site_id,
                Site.is_active.is_(True),
            )
        )

    async def get_reporting_period(
        self,
        *,
        company_id: UUID,
        reporting_period_id: UUID,
    ) -> ReportingPeriod | None:
        return await self.session.scalar(
            select(ReportingPeriod).where(
                ReportingPeriod.company_id == company_id,
                ReportingPeriod.id == reporting_period_id,
            )
        )

    async def actor_exists(self, *, company_id: UUID, actor_id: UUID) -> bool:
        identifier = await self.session.scalar(
            select(Actor.id).where(
                Actor.company_id == company_id,
                Actor.id == actor_id,
                Actor.is_active.is_(True),
            )
        )
        return identifier is not None

    async def agent_run_exists(self, *, company_id: UUID, agent_run_id: UUID) -> bool:
        identifier = await self.session.scalar(
            select(AgentRun.id).where(
                AgentRun.company_id == company_id,
                AgentRun.id == agent_run_id,
            )
        )
        return identifier is not None

    async def acquire_calculation_lock(
        self,
        *,
        company_id: UUID,
        site_id: UUID,
        reporting_period_id: UUID,
        metric_definition_id: UUID,
        material_code: str,
    ) -> None:
        """Serialize measurement writes for one frozen material context."""
        context_key = ":".join(
            (
                str(company_id),
                str(site_id),
                str(reporting_period_id),
                str(metric_definition_id),
                material_code.strip().casefold(),
            )
        )
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('carbonmesh.measurement'), hashtext(:context_key))"
            ),
            {"context_key": context_key},
        )

    async def list_active_metrics(
        self,
        *,
        company_id: UUID,
        key: str,
    ) -> list[MetricDefinition]:
        result = await self.session.scalars(
            select(MetricDefinition)
            .where(
                MetricDefinition.company_id == company_id,
                MetricDefinition.key == key,
                MetricDefinition.is_active.is_(True),
            )
            .order_by(MetricDefinition.version, MetricDefinition.id)
            .limit(2)
        )
        return list(result.all())

    async def list_active_methods(
        self,
        *,
        company_id: UUID,
        key: str,
        version: str,
        effective_on,
    ) -> list[MethodDefinition]:
        result = await self.session.scalars(
            select(MethodDefinition)
            .where(
                MethodDefinition.company_id == company_id,
                MethodDefinition.method_type == "measurement",
                MethodDefinition.key == key,
                MethodDefinition.version == version,
                MethodDefinition.is_active.is_(True),
                MethodDefinition.effective_from <= effective_on,
                (
                    MethodDefinition.effective_to.is_(None)
                    | (MethodDefinition.effective_to >= effective_on)
                ),
            )
            .order_by(MethodDefinition.id)
            .limit(2)
        )
        return list(result.all())

    async def list_activities(
        self,
        *,
        company_id: UUID,
        site_id: UUID,
        reporting_period_id: UUID,
        metric_definition_id: UUID,
        material_code: str,
        activity_record_ids: list[UUID] | None,
    ) -> list[ActivitySource]:
        statement = (
            select(ActivityRecord, SupplierProduct.product_code, RawActivityRecord)
            .join(
                RawActivityRecord,
                and_(
                    RawActivityRecord.company_id == ActivityRecord.company_id,
                    RawActivityRecord.id == ActivityRecord.raw_activity_record_id,
                ),
            )
            .outerjoin(
                SupplierProduct,
                and_(
                    SupplierProduct.company_id == ActivityRecord.company_id,
                    SupplierProduct.id == ActivityRecord.supplier_product_id,
                ),
            )
            .where(
                ActivityRecord.company_id == company_id,
                ActivityRecord.site_id == site_id,
                ActivityRecord.reporting_period_id == reporting_period_id,
                ActivityRecord.metric_definition_id == metric_definition_id,
                ActivityRecord.material_code == material_code,
                ActivityRecord.status == "valid",
            )
            .order_by(ActivityRecord.id)
            .limit(MAX_CALCULATION_ACTIVITY_ROWS + 1)
        )
        if activity_record_ids is not None:
            statement = statement.where(ActivityRecord.id.in_(activity_record_ids))
        rows = (await self.session.execute(statement)).all()
        return [
            ActivitySource(activity=row[0], product_code=row[1], raw_activity=row[2])
            for row in rows
        ]

    async def list_factor_candidates(
        self,
        *,
        company_id: UUID,
        metric_definition_id: UUID,
    ) -> list[EmissionFactor]:
        result = await self.session.scalars(
            select(EmissionFactor)
            .where(
                EmissionFactor.company_id == company_id,
                EmissionFactor.metric_definition_id == metric_definition_id,
                EmissionFactor.status == "active",
            )
            .order_by(EmissionFactor.id)
            .limit(MAX_FACTOR_CANDIDATES + 1)
        )
        return list(result.all())

    async def get_evidence_checksums(
        self,
        *,
        company_id: UUID,
        evidence_item_ids: set[UUID],
    ) -> dict[UUID, str]:
        if not evidence_item_ids:
            return {}
        rows = (
            await self.session.execute(
                select(EvidenceItem.id, EvidenceItem.checksum).where(
                    EvidenceItem.company_id == company_id,
                    EvidenceItem.id.in_(evidence_item_ids),
                )
            )
        ).all()
        return {row[0]: row[1] for row in rows}

    async def get_baseline(
        self,
        *,
        company_id: UUID,
        site_id: UUID,
        reporting_period_id: UUID,
        metric_definition_id: UUID,
    ) -> CarbonBaseline | None:
        return await self.session.scalar(
            select(CarbonBaseline).where(
                CarbonBaseline.company_id == company_id,
                CarbonBaseline.site_id == site_id,
                CarbonBaseline.reporting_period_id == reporting_period_id,
                CarbonBaseline.metric_definition_id == metric_definition_id,
            )
        )

    async def find_measurement_id_by_input_hash(
        self,
        *,
        company_id: UUID,
        input_hash: str,
    ) -> UUID | None:
        return await self.session.scalar(
            select(CarbonMeasurement.id)
            .join(
                CalculationRun,
                and_(
                    CalculationRun.company_id == CarbonMeasurement.company_id,
                    CalculationRun.id == CarbonMeasurement.calculation_run_id,
                ),
            )
            .where(
                CarbonMeasurement.company_id == company_id,
                CalculationRun.input_hash == input_hash,
            )
        )

    async def get_latest_verified_measurement(
        self,
        *,
        company_id: UUID,
        site_id: UUID,
        reporting_period_id: UUID,
        metric_definition_id: UUID,
        material_code: str,
    ) -> CarbonMeasurement | None:
        return await self.session.scalar(
            select(CarbonMeasurement)
            .join(
                CalculationRun,
                and_(
                    CalculationRun.company_id == CarbonMeasurement.company_id,
                    CalculationRun.id == CarbonMeasurement.calculation_run_id,
                ),
            )
            .where(
                CarbonMeasurement.company_id == company_id,
                CarbonMeasurement.site_id == site_id,
                CarbonMeasurement.reporting_period_id == reporting_period_id,
                CarbonMeasurement.metric_definition_id == metric_definition_id,
                CarbonMeasurement.status == "verified",
                CalculationRun.summary["material_code"].as_string() == material_code,
            )
            .order_by(CarbonMeasurement.created_at.desc(), CarbonMeasurement.id.desc())
            .limit(1)
        )

    async def persist_measurement(
        self,
        plan: MeasurementPersistencePlan,
    ) -> PersistedMeasurement:
        now = datetime.now(UTC)
        confidence = plan.confidence
        mass_items = [item for item in plan.items if isinstance(item, CalculationPersistenceItem)]
        grid_items = [
            item for item in plan.items if isinstance(item, GridCalculationPersistenceItem)
        ]
        summary = canonical_json_value(
            {
                "trace_id": plan.trace_id,
                "material_code": plan.material_code,
                "activity_record_ids": [item.source.activity.id for item in plan.items],
                "emission_factor_ids": sorted({item.factor.id for item in mass_items}, key=str),
                "grid_intensity_point_ids": [item.grid_point.id for item in grid_items],
                "confidence": asdict(confidence),
                "input_snapshot": plan.input_snapshot,
                "method_hash": sha256_payload(
                    {
                        "key": plan.context.method.key,
                        "version": plan.context.method.version,
                        "configuration": plan.context.method.configuration,
                    }
                ),
                "code_hash": measurement_code_hash(),
                "baseline": (
                    {
                        "id": plan.baseline.id,
                        "name": plan.baseline.name,
                        "value_kgco2e": plan.baseline.value_kgco2e,
                        "unit": plan.baseline.unit,
                        "frozen_hash": plan.baseline.frozen_hash,
                    }
                    if plan.baseline is not None
                    else None
                ),
                "variance_kgco2e": (
                    plan.variance.variance_kgco2e if plan.variance is not None else None
                ),
                "variance_pct": plan.variance.variance_pct if plan.variance is not None else None,
            }
        )
        run = CalculationRun(
            company_id=plan.company_id,
            agent_run_id=plan.agent_run_id,
            reporting_period_id=plan.context.reporting_period.id,
            method_definition_id=plan.context.method.id,
            method_version=plan.context.method.version,
            code_version=plan.context.method.code_version,
            rounding_policy=plan.rounding_policy,
            input_hash=plan.input_hash,
            output_hash=plan.output_hash,
            status="completed",
            started_at=now,
            completed_at=now,
            summary=summary,
        )
        self.session.add(run)
        await self.session.flush()

        calculations: list[EmissionCalculation] = []
        for item in plan.items:
            is_grid = isinstance(item, GridCalculationPersistenceItem)
            calculation = EmissionCalculation(
                company_id=plan.company_id,
                calculation_run_id=run.id,
                activity_record_id=item.source.activity.id,
                emission_factor_id=None if is_grid else item.factor.id,
                grid_intensity_point_id=item.grid_point.id if is_grid else None,
                normalized_quantity=(
                    item.normalized_quantity_kwh if is_grid else item.normalized_quantity_kg
                ),
                quantity_unit="kWh" if is_grid else "kg",
                factor_value=(
                    item.grid_point.intensity_gco2e_per_kwh
                    if is_grid
                    else item.factor_kgco2e_per_kg
                ),
                factor_unit="gCO2e/kWh" if is_grid else "kgCO2e/kg",
                emissions_kgco2e=item.emissions_kgco2e,
                formula=item.formula,
                output_hash=item.output_hash,
            )
            calculations.append(calculation)
        self.session.add_all(calculations)

        measurement = CarbonMeasurement(
            company_id=plan.company_id,
            calculation_run_id=run.id,
            site_id=plan.context.site.id,
            reporting_period_id=plan.context.reporting_period.id,
            metric_definition_id=plan.context.output_metric.id,
            value_kgco2e=plan.value_kgco2e,
            unit="kgCO2e",
            confidence=confidence.overall,
            status="verified",
            formula=plan.formula,
            output_hash=plan.output_hash,
            verified_at=now,
        )
        self.session.add(measurement)
        await self.session.flush()

        measurement_payload = canonical_json_value(
            {
                "measurement_id": measurement.id,
                "calculation_run_id": run.id,
                "company_id": plan.company_id,
                "site_id": plan.context.site.id,
                "reporting_period_id": plan.context.reporting_period.id,
                "metric_definition_id": plan.context.output_metric.id,
                "activity_record_ids": [item.source.activity.id for item in plan.items],
                "emission_factor_ids": sorted({item.factor.id for item in mass_items}, key=str),
                "grid_intensity_point_ids": [item.grid_point.id for item in grid_items],
                "value_kgco2e": plan.value_kgco2e,
                "unit": "kgCO2e",
                "confidence": confidence.overall,
                "formula": plan.formula,
                "output_hash": plan.output_hash,
                "method_version": plan.context.method.version,
                "code_version": plan.context.method.code_version,
                "method_hash": summary["method_hash"],
                "code_hash": summary["code_hash"],
                "coverage": plan.input_snapshot.get("coverage") if plan.input_snapshot else None,
            }
        )
        measurement_event = LedgerEvent(
            company_id=plan.company_id,
            event_type="measurement.verified",
            entity_type="carbon_measurement",
            entity_id=measurement.id,
            payload=measurement_payload,
            payload_hash=sha256_payload(measurement_payload),
            analysis_signature=plan.input_hash,
            created_by=plan.actor_id,
            supersedes_event_id=(
                plan.supersedes_measurement.ledger_event_id
                if plan.supersedes_measurement is not None
                else None
            ),
        )
        self.session.add(measurement_event)

        activity_events: dict[UUID, LedgerEvent] = {}
        for item in plan.items:
            activity = item.source.activity
            payload = canonical_json_value(
                {
                    "activity_record_id": activity.id,
                    "raw_activity_record_id": activity.raw_activity_record_id,
                    "data_source_id": item.source.raw_activity.data_source_id,
                    "source_document_id": item.source.raw_activity.source_document_id,
                    "row_key": item.source.raw_activity.row_key,
                    "row_number": item.source.raw_activity.row_number,
                    "raw_checksum": item.source.raw_activity.checksum,
                    "source_quantity": activity.quantity,
                    "source_unit": activity.unit,
                    "normalized_quantity": (
                        item.normalized_quantity_kwh
                        if isinstance(item, GridCalculationPersistenceItem)
                        else item.normalized_quantity_kg
                    ),
                    "normalized_unit": activity.normalized_unit,
                    "interval_start": (
                        item.interval_start
                        if isinstance(item, GridCalculationPersistenceItem)
                        else None
                    ),
                    "material_code": activity.material_code,
                    "product_code": item.source.product_code,
                }
            )
            event = LedgerEvent(
                company_id=plan.company_id,
                event_type="measurement.activity_used",
                entity_type="activity_record",
                entity_id=activity.id,
                payload=payload,
                payload_hash=sha256_payload(payload),
                analysis_signature=plan.input_hash,
                created_by=plan.actor_id,
            )
            activity_events[activity.id] = event
            self.session.add(event)

        factor_events: dict[UUID, LedgerEvent] = {}
        factors = {item.factor.id: item.factor for item in mass_items}
        for factor_id, factor in factors.items():
            payload = canonical_json_value(
                {
                    "emission_factor_id": factor.id,
                    "factor_code": factor.factor_code,
                    "version": factor.version,
                    "factor_value": factor.factor_value,
                    "numerator_unit": factor.numerator_unit,
                    "denominator_unit": factor.denominator_unit,
                    "evidence_item_id": factor.evidence_item_id,
                }
            )
            event = LedgerEvent(
                company_id=plan.company_id,
                event_type="measurement.factor_used",
                entity_type="emission_factor",
                entity_id=factor_id,
                payload=payload,
                payload_hash=sha256_payload(payload),
                analysis_signature=plan.input_hash,
                created_by=plan.actor_id,
            )
            factor_events[factor_id] = event
            self.session.add(event)

        grid_events: dict[UUID, LedgerEvent] = {}
        for item in grid_items:
            point = item.grid_point
            payload = canonical_json_value(
                {
                    "grid_intensity_point_id": point.id,
                    "observed_at": point.observed_at,
                    "zone": point.zone,
                    "provider": point.provider,
                    "intensity_gco2e_per_kwh": point.intensity_gco2e_per_kwh,
                    "point_hash": point.point_hash,
                    "method_version": point.method_version,
                    "source_document_id": point.source_document_id,
                    "evidence_item_id": point.evidence_item_id,
                    "is_estimated": point.is_estimated,
                }
            )
            event = LedgerEvent(
                company_id=plan.company_id,
                event_type="measurement.grid_point_used",
                entity_type="grid_intensity_point",
                entity_id=point.id,
                payload=payload,
                payload_hash=sha256_payload(payload),
                analysis_signature=plan.input_hash,
                created_by=plan.actor_id,
            )
            grid_events[point.id] = event
            self.session.add(event)

        baseline_event: LedgerEvent | None = None
        if plan.baseline is not None:
            payload = canonical_json_value(
                {
                    "carbon_baseline_id": plan.baseline.id,
                    "value_kgco2e": plan.baseline.value_kgco2e,
                    "unit": plan.baseline.unit,
                    "frozen_hash": plan.baseline.frozen_hash,
                }
            )
            baseline_event = LedgerEvent(
                company_id=plan.company_id,
                event_type="measurement.baseline_used",
                entity_type="carbon_baseline",
                entity_id=plan.baseline.id,
                payload=payload,
                payload_hash=sha256_payload(payload),
                analysis_signature=plan.input_hash,
                created_by=plan.actor_id,
            )
            self.session.add(baseline_event)

        await self.session.flush()
        measurement.ledger_event_id = measurement_event.id
        if plan.supersedes_measurement is not None:
            plan.supersedes_measurement.status = "superseded"

        for event in activity_events.values():
            self.session.add(
                LineageEdge(
                    company_id=plan.company_id,
                    parent_event_id=event.id,
                    child_event_id=measurement_event.id,
                    relationship_type="derived_from_activity",
                    edge_metadata={"calculation_run_id": str(run.id)},
                )
            )
        for event in factor_events.values():
            self.session.add(
                LineageEdge(
                    company_id=plan.company_id,
                    parent_event_id=event.id,
                    child_event_id=measurement_event.id,
                    relationship_type="used_emission_factor",
                    edge_metadata={"calculation_run_id": str(run.id)},
                )
            )
        for event in grid_events.values():
            self.session.add(
                LineageEdge(
                    company_id=plan.company_id,
                    parent_event_id=event.id,
                    child_event_id=measurement_event.id,
                    relationship_type="used_grid_point",
                    edge_metadata={"calculation_run_id": str(run.id)},
                )
            )
        if baseline_event is not None:
            self.session.add(
                LineageEdge(
                    company_id=plan.company_id,
                    parent_event_id=baseline_event.id,
                    child_event_id=measurement_event.id,
                    relationship_type="compared_with_baseline",
                    edge_metadata={},
                )
            )

        measurement_evidence_ids: set[UUID] = set()
        for item in grid_items:
            point = item.grid_point
            self.session.add(
                LedgerEventEvidence(
                    company_id=plan.company_id,
                    ledger_event_id=grid_events[point.id].id,
                    evidence_item_id=point.evidence_item_id,
                    relevance="Supports hourly grid intensity",
                )
            )
            if point.evidence_item_id not in measurement_evidence_ids:
                measurement_evidence_ids.add(point.evidence_item_id)
                self.session.add(
                    LedgerEventEvidence(
                        company_id=plan.company_id,
                        ledger_event_id=measurement_event.id,
                        evidence_item_id=point.evidence_item_id,
                        relevance="Supports verified Scope 2",
                    )
                )
        for factor_id, factor in factors.items():
            factor_event = factor_events[factor_id]
            self.session.add(
                LedgerEventEvidence(
                    company_id=plan.company_id,
                    ledger_event_id=factor_event.id,
                    evidence_item_id=factor.evidence_item_id,
                    relevance="Supports selected emission factor",
                )
            )
            if factor.evidence_item_id not in measurement_evidence_ids:
                measurement_evidence_ids.add(factor.evidence_item_id)
                self.session.add(
                    LedgerEventEvidence(
                        company_id=plan.company_id,
                        ledger_event_id=measurement_event.id,
                        evidence_item_id=factor.evidence_item_id,
                        relevance="Supports verified measurement",
                    )
                )

        variance_alert: VarianceAlert | None = None
        if plan.baseline is not None and plan.variance is not None:
            absolute_pct = abs(plan.variance.variance_pct or Decimal(0))
            severity = (
                "critical"
                if absolute_pct >= Decimal(20)
                else "warning"
                if absolute_pct >= Decimal(5)
                else "info"
            )
            variance_alert = VarianceAlert(
                company_id=plan.company_id,
                carbon_measurement_id=measurement.id,
                carbon_baseline_id=plan.baseline.id,
                variance_kgco2e=plan.variance.variance_kgco2e,
                variance_pct=plan.variance.variance_pct,
                severity=severity,
                status="open",
            )
            self.session.add(variance_alert)

        audit = AuditLog(
            company_id=plan.company_id,
            actor_id=plan.actor_id,
            agent_run_id=plan.agent_run_id,
            action="measurement.calculated",
            entity_type="carbon_measurement",
            entity_id=measurement.id,
            trace_id=plan.trace_id,
            details=canonical_json_value(
                {
                    "calculation_run_id": run.id,
                    "ledger_event_id": measurement_event.id,
                    "input_hash": plan.input_hash,
                    "output_hash": plan.output_hash,
                    "activity_count": len(plan.items),
                }
            ),
        )
        self.session.add(audit)
        await self.session.flush()

        return PersistedMeasurement(
            measurement_id=measurement.id,
            calculation_run_id=run.id,
            ledger_event_id=measurement_event.id,
            variance_alert_id=variance_alert.id if variance_alert is not None else None,
            audit_log_id=audit.id,
        )

    async def get_detail_bundle(
        self,
        *,
        company_id: UUID,
        measurement_id: UUID,
    ) -> MeasurementDetailBundle | None:
        base_row = (
            await self.session.execute(
                select(
                    CarbonMeasurement,
                    CalculationRun,
                    MetricDefinition,
                    MethodDefinition,
                    Site,
                    ReportingPeriod,
                )
                .join(
                    CalculationRun,
                    and_(
                        CalculationRun.company_id == CarbonMeasurement.company_id,
                        CalculationRun.id == CarbonMeasurement.calculation_run_id,
                    ),
                )
                .join(
                    MetricDefinition,
                    and_(
                        MetricDefinition.company_id == CarbonMeasurement.company_id,
                        MetricDefinition.id == CarbonMeasurement.metric_definition_id,
                    ),
                )
                .join(
                    MethodDefinition,
                    and_(
                        MethodDefinition.company_id == CalculationRun.company_id,
                        MethodDefinition.id == CalculationRun.method_definition_id,
                    ),
                )
                .join(
                    Site,
                    and_(
                        Site.company_id == CarbonMeasurement.company_id,
                        Site.id == CarbonMeasurement.site_id,
                    ),
                )
                .join(
                    ReportingPeriod,
                    and_(
                        ReportingPeriod.company_id == CarbonMeasurement.company_id,
                        ReportingPeriod.id == CarbonMeasurement.reporting_period_id,
                    ),
                )
                .where(
                    CarbonMeasurement.company_id == company_id,
                    CarbonMeasurement.id == measurement_id,
                )
            )
        ).one_or_none()
        if base_row is None:
            return None
        measurement, run, metric, method, site, period = base_row

        calculation_rows = (
            await self.session.execute(
                select(
                    EmissionCalculation,
                    ActivityRecord,
                    SupplierProduct.product_code,
                    RawActivityRecord,
                    EmissionFactor,
                    EvidenceItem,
                    SourceDocument,
                )
                .join(
                    ActivityRecord,
                    and_(
                        ActivityRecord.company_id == EmissionCalculation.company_id,
                        ActivityRecord.id == EmissionCalculation.activity_record_id,
                    ),
                )
                .outerjoin(
                    SupplierProduct,
                    and_(
                        SupplierProduct.company_id == ActivityRecord.company_id,
                        SupplierProduct.id == ActivityRecord.supplier_product_id,
                    ),
                )
                .join(
                    RawActivityRecord,
                    and_(
                        RawActivityRecord.company_id == ActivityRecord.company_id,
                        RawActivityRecord.id == ActivityRecord.raw_activity_record_id,
                    ),
                )
                .join(
                    EmissionFactor,
                    and_(
                        EmissionFactor.company_id == EmissionCalculation.company_id,
                        EmissionFactor.id == EmissionCalculation.emission_factor_id,
                    ),
                )
                .join(
                    EvidenceItem,
                    and_(
                        EvidenceItem.company_id == EmissionFactor.company_id,
                        EvidenceItem.id == EmissionFactor.evidence_item_id,
                    ),
                )
                .join(
                    SourceDocument,
                    and_(
                        SourceDocument.company_id == EvidenceItem.company_id,
                        SourceDocument.id == EvidenceItem.source_document_id,
                    ),
                )
                .where(
                    EmissionCalculation.company_id == company_id,
                    EmissionCalculation.calculation_run_id == run.id,
                )
                .order_by(EmissionCalculation.activity_record_id)
            )
        ).all()

        grid_calculation_rows = []
        if metric.key == "emissions.scope2.location_based":
            grid_calculation_rows = (
                await self.session.execute(
                    select(
                        EmissionCalculation,
                        ActivityRecord,
                        RawActivityRecord,
                        GridIntensityPoint,
                        EvidenceItem,
                        SourceDocument,
                    )
                    .join(
                        ActivityRecord,
                        and_(
                            ActivityRecord.company_id == EmissionCalculation.company_id,
                            ActivityRecord.id == EmissionCalculation.activity_record_id,
                        ),
                    )
                    .join(
                        RawActivityRecord,
                        and_(
                            RawActivityRecord.company_id == ActivityRecord.company_id,
                            RawActivityRecord.id == ActivityRecord.raw_activity_record_id,
                        ),
                    )
                    .join(
                        GridIntensityPoint,
                        and_(
                            GridIntensityPoint.company_id == EmissionCalculation.company_id,
                            GridIntensityPoint.id == EmissionCalculation.grid_intensity_point_id,
                        ),
                    )
                    .join(
                        EvidenceItem,
                        and_(
                            EvidenceItem.company_id == GridIntensityPoint.company_id,
                            EvidenceItem.id == GridIntensityPoint.evidence_item_id,
                        ),
                    )
                    .join(
                        SourceDocument,
                        and_(
                            SourceDocument.company_id == EvidenceItem.company_id,
                            SourceDocument.id == EvidenceItem.source_document_id,
                        ),
                    )
                    .where(
                        EmissionCalculation.company_id == company_id,
                        EmissionCalculation.calculation_run_id == run.id,
                    )
                    .order_by(
                        GridIntensityPoint.observed_at, EmissionCalculation.activity_record_id
                    )
                )
            ).all()

        baseline = None
        summary = run.summary if isinstance(run.summary, dict) else {}
        baseline_was_frozen = "baseline" in summary
        summary_baseline = summary.get("baseline")
        summary_baseline_id = (
            summary_baseline.get("id") if isinstance(summary_baseline, dict) else None
        )
        if summary_baseline_id is not None:
            baseline = await self.session.scalar(
                select(CarbonBaseline).where(
                    CarbonBaseline.company_id == company_id,
                    CarbonBaseline.id == UUID(str(summary_baseline_id)),
                )
            )
        if baseline is None and not baseline_was_frozen:
            baseline = await self.get_baseline(
                company_id=company_id,
                site_id=measurement.site_id,
                reporting_period_id=measurement.reporting_period_id,
                metric_definition_id=measurement.metric_definition_id,
            )
        variance_alert = None
        if baseline is not None:
            variance_alert = await self.session.scalar(
                select(VarianceAlert).where(
                    VarianceAlert.company_id == company_id,
                    VarianceAlert.carbon_measurement_id == measurement.id,
                    VarianceAlert.carbon_baseline_id == baseline.id,
                )
            )
        audit_log_id = await self.session.scalar(
            select(AuditLog.id)
            .where(
                AuditLog.company_id == company_id,
                AuditLog.entity_type == "carbon_measurement",
                AuditLog.entity_id == measurement.id,
                AuditLog.action == "measurement.calculated",
            )
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
        return MeasurementDetailBundle(
            measurement=measurement,
            calculation_run=run,
            metric=metric,
            method=method,
            site=site,
            reporting_period=period,
            audit_log_id=audit_log_id,
            calculations=tuple(calculation_rows),
            baseline=baseline,
            variance_alert=variance_alert,
            grid_calculations=tuple(grid_calculation_rows),
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
    ) -> tuple[list[tuple[CarbonMeasurement, MetricDefinition, Site, ReportingPeriod]], int]:
        filters = [CarbonMeasurement.company_id == company_id]
        if site_id is not None:
            filters.append(CarbonMeasurement.site_id == site_id)
        if reporting_period_id is not None:
            filters.append(CarbonMeasurement.reporting_period_id == reporting_period_id)
        if category is not None:
            filters.append(MetricDefinition.key == category)
        if status is not None:
            filters.append(CarbonMeasurement.status == status)

        joins: Select = (
            select(CarbonMeasurement, MetricDefinition, Site, ReportingPeriod)
            .join(
                MetricDefinition,
                and_(
                    MetricDefinition.company_id == CarbonMeasurement.company_id,
                    MetricDefinition.id == CarbonMeasurement.metric_definition_id,
                ),
            )
            .join(
                Site,
                and_(
                    Site.company_id == CarbonMeasurement.company_id,
                    Site.id == CarbonMeasurement.site_id,
                ),
            )
            .join(
                ReportingPeriod,
                and_(
                    ReportingPeriod.company_id == CarbonMeasurement.company_id,
                    ReportingPeriod.id == CarbonMeasurement.reporting_period_id,
                ),
            )
            .where(*filters)
        )
        rows = (
            await self.session.execute(
                joins.order_by(CarbonMeasurement.created_at.desc(), CarbonMeasurement.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()

        count_statement = (
            select(func.count())
            .select_from(CarbonMeasurement)
            .join(
                MetricDefinition,
                and_(
                    MetricDefinition.company_id == CarbonMeasurement.company_id,
                    MetricDefinition.id == CarbonMeasurement.metric_definition_id,
                ),
            )
            .where(*filters)
        )
        total = int((await self.session.scalar(count_statement)) or 0)
        return list(rows), total
