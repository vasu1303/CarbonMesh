"""Persistence-only queries for the Assurance module."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models.ai import AgentRun
from app.db.models.assurance import (
    ClaimCitation,
    DisclosureClaim,
    DisclosureDraft,
    DisclosureRequirement,
    EvidenceGap,
    Standard,
)
from app.db.models.carbon import CalculationRun, CarbonMeasurement
from app.db.models.core import (
    Actor,
    Approval,
    Company,
    DataSource,
    EvidenceItem,
    ReportingPeriod,
    Site,
    SourceDocument,
)
from app.db.models.ledger import FactBinding, LedgerEvent
from app.db.models.semantic import MethodDefinition, MetricDefinition
from app.modules.assurance.retrieval import EvidenceRetrievalContext

ApprovalStatus = Literal["pending", "approved", "rejected", "invalidated", "expired"]


@dataclass(frozen=True, slots=True)
class DraftDependencies:
    company: Company
    standard: Standard
    site: Site
    reporting_period: ReportingPeriod
    measurement: CarbonMeasurement
    measurement_metric: MetricDefinition
    requested_by: Actor
    agent_run: AgentRun | None
    measurement_coverage: dict[str, Any] | None = None
    measurement_calculation: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence: EvidenceItem
    document: SourceDocument
    source: DataSource
    similarity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class DraftAggregate:
    draft: DisclosureDraft
    standard: Standard
    requirements: tuple[DisclosureRequirement, ...]
    measurement: CarbonMeasurement | None
    claims: tuple[DisclosureClaim, ...]
    citations: tuple[ClaimCitation, ...]
    gaps: tuple[EvidenceGap, ...]
    fact_bindings: tuple[FactBinding, ...]
    evidence: tuple[EvidenceRecord, ...]
    ledger_events: tuple[LedgerEvent, ...]
    approval: Approval | None


class AssuranceRepository:
    """Tenant-bounded persistence primitives; callers own transactions and policy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_standards(
        self,
        *,
        company_id: UUID,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[Standard], int]:
        predicates = [Standard.company_id == company_id]
        if active_only:
            predicates.append(Standard.is_active.is_(True))
        total = await self.session.scalar(select(func.count(Standard.id)).where(*predicates))
        standards = list(
            await self.session.scalars(
                select(Standard)
                .where(*predicates)
                .order_by(Standard.code, Standard.version, Standard.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return standards, int(total or 0)

    async def get_standard(self, *, company_id: UUID, standard_id: UUID) -> Standard | None:
        return await self.session.scalar(
            select(Standard).where(
                Standard.company_id == company_id,
                Standard.id == standard_id,
            )
        )

    async def list_requirements(
        self,
        *,
        company_id: UUID,
        standard_id: UUID,
        active_only: bool = True,
    ) -> list[DisclosureRequirement]:
        predicates = [
            DisclosureRequirement.company_id == company_id,
            DisclosureRequirement.standard_id == standard_id,
        ]
        if active_only:
            predicates.append(DisclosureRequirement.is_active.is_(True))
        return list(
            await self.session.scalars(
                select(DisclosureRequirement)
                .where(*predicates)
                .order_by(DisclosureRequirement.sequence, DisclosureRequirement.id)
            )
        )

    async def get_measurement(
        self,
        *,
        company_id: UUID,
        measurement_id: UUID,
    ) -> CarbonMeasurement | None:
        return await self.session.scalar(
            select(CarbonMeasurement).where(
                CarbonMeasurement.company_id == company_id,
                CarbonMeasurement.id == measurement_id,
            )
        )

    async def get_actor(self, *, company_id: UUID, actor_id: UUID) -> Actor | None:
        return await self.session.scalar(
            select(Actor).where(Actor.company_id == company_id, Actor.id == actor_id)
        )

    async def get_agent_run(
        self,
        *,
        company_id: UUID,
        agent_run_id: UUID,
    ) -> AgentRun | None:
        return await self.session.scalar(
            select(AgentRun).where(
                AgentRun.company_id == company_id,
                AgentRun.id == agent_run_id,
            )
        )

    async def load_draft_dependencies(
        self,
        *,
        company_id: UUID,
        standard_id: UUID,
        site_id: UUID,
        reporting_period_id: UUID,
        measurement_id: UUID,
        requested_by: UUID,
        agent_run_id: UUID | None,
    ) -> DraftDependencies | None:
        company = await self.session.scalar(select(Company).where(Company.id == company_id))
        standard = await self.get_standard(company_id=company_id, standard_id=standard_id)
        site = await self.session.scalar(
            select(Site).where(Site.company_id == company_id, Site.id == site_id)
        )
        period = await self.session.scalar(
            select(ReportingPeriod).where(
                ReportingPeriod.company_id == company_id,
                ReportingPeriod.id == reporting_period_id,
            )
        )
        measurement = await self.get_measurement(
            company_id=company_id,
            measurement_id=measurement_id,
        )
        measurement_metric = (
            await self.session.scalar(
                select(MetricDefinition).where(
                    MetricDefinition.company_id == company_id,
                    MetricDefinition.id == measurement.metric_definition_id,
                )
            )
            if measurement is not None
            else None
        )
        actor = await self.get_actor(company_id=company_id, actor_id=requested_by)
        agent_run = (
            await self.get_agent_run(company_id=company_id, agent_run_id=agent_run_id)
            if agent_run_id is not None
            else None
        )
        if (
            company is None
            or standard is None
            or site is None
            or period is None
            or measurement is None
            or measurement_metric is None
            or actor is None
            or (agent_run_id is not None and agent_run is None)
        ):
            return None
        calculation = (
            await self.session.execute(
                select(CalculationRun, MethodDefinition)
                .join(MethodDefinition, MethodDefinition.id == CalculationRun.method_definition_id)
                .where(
                    CalculationRun.company_id == company_id,
                    MethodDefinition.company_id == company_id,
                    CalculationRun.id == measurement.calculation_run_id,
                )
            )
        ).one_or_none()
        if calculation is None:
            return None
        coverage = None
        run, method = calculation
        snapshot = run.summary.get("input_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        is_scope2 = "measurement.scope2.location_based.hourly" in (
            method.key,
            snapshot.get("method_key"),
        )
        if is_scope2:
            coverage = snapshot.get("coverage", {})
            if not isinstance(coverage, dict):
                coverage = {}
        calculation_identity = {
            "id": run.id,
            "input_hash": run.input_hash,
            "output_hash": run.output_hash,
            "status": run.status,
            "reporting_period_id": run.reporting_period_id,
            "run_method_version": run.method_version,
            "run_code_version": run.code_version,
            "rounding_policy": run.rounding_policy,
            "recorded_method_hash": run.summary.get("method_hash"),
            "method_id": method.id,
            "method_key": method.key,
            "method_version": method.version,
            "code_version": method.code_version,
            "configuration": method.configuration,
            "effective_from": method.effective_from,
            "effective_to": method.effective_to,
            "is_active": method.is_active,
            "frozen_method": {
                field: snapshot.get(field)
                for field in (
                    "method_definition_id",
                    "method_key",
                    "method_version",
                    "code_version",
                    "method_configuration",
                )
            }
            if is_scope2 or snapshot.get("method_key") is not None
            else None,
        }
        return DraftDependencies(
            company=company,
            standard=standard,
            site=site,
            reporting_period=period,
            measurement=measurement,
            measurement_metric=measurement_metric,
            requested_by=actor,
            agent_run=agent_run,
            measurement_coverage=coverage,
            measurement_calculation=calculation_identity,
        )

    async def allocate_draft_version(
        self,
        *,
        company_id: UUID,
        standard_id: UUID,
        reporting_period_id: UUID,
    ) -> int | None:
        """Serialize and allocate the next version across every site for the context."""

        locked_standard = await self.session.scalar(
            select(Standard.id)
            .where(Standard.company_id == company_id, Standard.id == standard_id)
            .with_for_update()
        )
        if locked_standard is None:
            return None
        current = await self.session.scalar(
            select(func.max(DisclosureDraft.version)).where(
                DisclosureDraft.company_id == company_id,
                DisclosureDraft.standard_id == standard_id,
                DisclosureDraft.reporting_period_id == reporting_period_id,
            )
        )
        return int(current or 0) + 1

    async def find_draft_by_idempotency_key(
        self,
        *,
        company_id: UUID,
        idempotency_key: str,
        standard_id: UUID | None = None,
        reporting_period_id: UUID | None = None,
    ) -> DisclosureDraft | None:
        predicates = [
            DisclosureDraft.company_id == company_id,
            DisclosureDraft.validation_summary["idempotency_key"].as_string() == idempotency_key,
        ]
        if standard_id is not None:
            predicates.append(DisclosureDraft.standard_id == standard_id)
        if reporting_period_id is not None:
            predicates.append(DisclosureDraft.reporting_period_id == reporting_period_id)
        return await self.session.scalar(
            select(DisclosureDraft)
            .where(*predicates)
            .order_by(DisclosureDraft.created_at.desc(), DisclosureDraft.id.desc())
            .limit(1)
        )

    async def create_draft(
        self,
        *,
        company_id: UUID,
        standard_id: UUID,
        site_id: UUID,
        reporting_period_id: UUID,
        agent_run_id: UUID | None,
        version: int,
        title: str,
        narrative_template: str,
        context_hash: str,
        payload_hash: str,
        validation_summary: dict[str, Any],
        status: str = "draft",
    ) -> DisclosureDraft:
        draft = DisclosureDraft(
            company_id=company_id,
            standard_id=standard_id,
            site_id=site_id,
            reporting_period_id=reporting_period_id,
            agent_run_id=agent_run_id,
            version=version,
            title=title,
            narrative_template=narrative_template,
            context_hash=context_hash,
            payload_hash=payload_hash,
            status=status,
            validation_summary=validation_summary,
        )
        self.session.add(draft)
        await self.session.flush()
        return draft

    async def get_draft(
        self,
        *,
        company_id: UUID,
        draft_id: UUID,
        for_update: bool = False,
    ) -> DisclosureDraft | None:
        statement = select(DisclosureDraft).where(
            DisclosureDraft.company_id == company_id,
            DisclosureDraft.id == draft_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_claims(
        self,
        *,
        company_id: UUID,
        draft_id: UUID,
    ) -> list[DisclosureClaim]:
        return list(
            await self.session.scalars(
                select(DisclosureClaim)
                .where(
                    DisclosureClaim.company_id == company_id,
                    DisclosureClaim.disclosure_draft_id == draft_id,
                )
                .order_by(DisclosureClaim.sequence, DisclosureClaim.id)
            )
        )

    async def list_citations(
        self,
        *,
        company_id: UUID,
        claim_ids: Collection[UUID],
    ) -> list[ClaimCitation]:
        if not claim_ids:
            return []
        return list(
            await self.session.scalars(
                select(ClaimCitation)
                .where(
                    ClaimCitation.company_id == company_id,
                    ClaimCitation.disclosure_claim_id.in_(claim_ids),
                )
                .order_by(
                    ClaimCitation.disclosure_claim_id,
                    ClaimCitation.created_at,
                    ClaimCitation.id,
                )
            )
        )

    async def list_gaps(
        self,
        *,
        company_id: UUID,
        draft_id: UUID,
    ) -> list[EvidenceGap]:
        return list(
            await self.session.scalars(
                select(EvidenceGap)
                .where(
                    EvidenceGap.company_id == company_id,
                    EvidenceGap.disclosure_draft_id == draft_id,
                )
                .order_by(EvidenceGap.created_at, EvidenceGap.code, EvidenceGap.id)
            )
        )

    async def get_citation_by_sources(
        self,
        *,
        company_id: UUID,
        claim_id: UUID,
        ledger_event_id: UUID | None,
        evidence_item_id: UUID | None,
    ) -> ClaimCitation | None:
        ledger_predicate = (
            ClaimCitation.ledger_event_id.is_(None)
            if ledger_event_id is None
            else ClaimCitation.ledger_event_id == ledger_event_id
        )
        evidence_predicate = (
            ClaimCitation.evidence_item_id.is_(None)
            if evidence_item_id is None
            else ClaimCitation.evidence_item_id == evidence_item_id
        )
        return await self.session.scalar(
            select(ClaimCitation).where(
                ClaimCitation.company_id == company_id,
                ClaimCitation.disclosure_claim_id == claim_id,
                ledger_predicate,
                evidence_predicate,
            )
        )

    async def create_claim(self, **values: Any) -> DisclosureClaim:
        claim = DisclosureClaim(**values)
        self.session.add(claim)
        await self.session.flush()
        return claim

    async def create_citation(self, **values: Any) -> ClaimCitation:
        citation = ClaimCitation(**values)
        self.session.add(citation)
        await self.session.flush()
        return citation

    async def create_gap(self, **values: Any) -> EvidenceGap:
        gap = EvidenceGap(**values)
        self.session.add(gap)
        await self.session.flush()
        return gap

    async def list_evidence_candidates(
        self,
        *,
        company_id: UUID,
        evidence_types: Collection[str],
        embedding_model: str,
        limit: int,
        query_embedding: Sequence[float] | None = None,
        evidence_item_ids: Collection[UUID] | None = None,
        context: EvidenceRetrievalContext | None = None,
    ) -> list[EvidenceRecord]:
        if not evidence_types or evidence_item_ids is not None and not evidence_item_ids:
            return []
        predicates = [
            EvidenceItem.company_id == company_id,
            EvidenceItem.evidence_type.in_(evidence_types),
            EvidenceItem.embedding.is_not(None),
            EvidenceItem.embedding_model == embedding_model,
            EvidenceItem.embedded_at.is_not(None),
            DataSource.status == "ready",
        ]
        if evidence_item_ids is not None:
            predicates.append(EvidenceItem.id.in_(evidence_item_ids))
        metadata = EvidenceItem.evidence_metadata
        newer_document = aliased(SourceDocument)
        predicates.extend(
            [
                # Exclude revoked/untrusted and superseded source versions before top-k.
                or_(
                    metadata["trust_status"].astext.in_(("accepted", "verified", "synthetic")),
                    and_(
                        metadata["trust_status"].astext.is_(None), DataSource.is_synthetic.is_(True)
                    ),
                ),
                or_(
                    metadata["source_version"].astext.is_(None),
                    metadata["source_version"].astext == cast(SourceDocument.version, String),
                ),
                or_(
                    metadata["source_checksum"].astext.is_(None),
                    metadata["source_checksum"].astext == SourceDocument.checksum,
                ),
                ~select(newer_document.id)
                .where(
                    newer_document.company_id == SourceDocument.company_id,
                    newer_document.data_source_id == SourceDocument.data_source_id,
                    newer_document.version > SourceDocument.version,
                )
                .exists(),
            ]
        )
        if context is not None:
            predicates.extend(
                [
                    metadata["requirement_codes"].contains([context.requirement_code]),
                    or_(
                        metadata["company_id"].astext.is_(None),
                        metadata["company_id"].astext == str(context.company_id),
                    ),
                    or_(
                        metadata["site_id"].astext.is_(None),
                        metadata["site_id"].astext == str(context.site_id),
                    ),
                    or_(
                        metadata["reporting_period_id"].astext.is_(None),
                        metadata["reporting_period_id"].astext == str(context.reporting_period_id),
                    ),
                    or_(
                        DataSource.site_id == context.site_id,
                        metadata["site_id"].astext == str(context.site_id),
                        func.btrim(metadata["site"].astext) == context.site_name.strip()
                        if context.site_name
                        else False,
                    ),
                    or_(
                        metadata["reporting_period_id"].astext == str(context.reporting_period_id),
                        func.btrim(metadata["reporting_period"].astext)
                        == context.reporting_period_name.strip()
                        if context.reporting_period_name
                        else False,
                    ),
                ]
            )
            for key, expected in (
                ("company", context.company_name),
                ("site", context.site_name),
                ("reporting_period", context.reporting_period_name),
            ):
                if expected is not None:
                    predicates.append(
                        or_(
                            metadata[key].astext.is_(None),
                            func.btrim(metadata[key].astext) == expected.strip(),
                        )
                    )
        base = (
            select(EvidenceItem, SourceDocument, DataSource)
            .join(
                SourceDocument,
                (SourceDocument.company_id == EvidenceItem.company_id)
                & (SourceDocument.id == EvidenceItem.source_document_id),
            )
            .join(
                DataSource,
                (DataSource.company_id == SourceDocument.company_id)
                & (DataSource.id == SourceDocument.data_source_id),
            )
            .where(*predicates)
        )
        if query_embedding is not None and len(query_embedding) != 768:
            raise ValueError("query_embedding must contain exactly 768 values")

        embedding_type = EvidenceItem.__table__.c.embedding.type
        if query_embedding is None or not isinstance(embedding_type, VECTOR):
            rows = (
                await self.session.execute(
                    base.order_by(
                        SourceDocument.checksum,
                        EvidenceItem.locator,
                        EvidenceItem.id,
                    ).limit(limit)
                )
            ).all()
            return [EvidenceRecord(row[0], row[1], row[2]) for row in rows]

        distance = EvidenceItem.embedding.cosine_distance(list(query_embedding))
        ranked_rows = (
            await self.session.execute(
                base.add_columns((1 - distance).label("similarity"))
                .order_by(
                    distance,
                    SourceDocument.checksum,
                    EvidenceItem.locator,
                    EvidenceItem.id,
                )
                .limit(limit)
            )
        ).all()
        return [
            EvidenceRecord(
                evidence=row[0],
                document=row[1],
                source=row[2],
                similarity=Decimal(str(row[3])),
            )
            for row in ranked_rows
        ]

    async def get_ledger_event(
        self,
        *,
        company_id: UUID,
        event_id: UUID,
    ) -> LedgerEvent | None:
        return await self.session.scalar(
            select(LedgerEvent).where(
                LedgerEvent.company_id == company_id,
                LedgerEvent.id == event_id,
            )
        )

    async def list_ledger_events(
        self,
        *,
        company_id: UUID,
        event_ids: Collection[UUID],
    ) -> list[LedgerEvent]:
        if not event_ids:
            return []
        return list(
            await self.session.scalars(
                select(LedgerEvent)
                .where(
                    LedgerEvent.company_id == company_id,
                    LedgerEvent.id.in_(event_ids),
                )
                .order_by(LedgerEvent.created_at, LedgerEvent.id)
            )
        )

    async def list_fact_bindings(
        self,
        *,
        company_id: UUID,
        draft_id: UUID,
    ) -> list[FactBinding]:
        return list(
            await self.session.scalars(
                select(FactBinding)
                .where(
                    FactBinding.company_id == company_id,
                    FactBinding.artifact_type == "disclosure_draft",
                    FactBinding.artifact_id == draft_id,
                )
                .order_by(FactBinding.placeholder, FactBinding.id)
            )
        )

    async def get_generic_approval(
        self,
        *,
        company_id: UUID,
        target_id: UUID,
        target_type: str = "disclosure_draft",
        status: ApprovalStatus | None = None,
    ) -> Approval | None:
        predicates = [
            Approval.company_id == company_id,
            Approval.target_type == target_type,
            Approval.target_id == target_id,
        ]
        if status is not None:
            predicates.append(Approval.status == status)
        return await self.session.scalar(
            select(Approval)
            .where(*predicates)
            .order_by(Approval.created_at.desc(), Approval.id.desc())
            .limit(1)
        )

    async def load_draft_aggregate(
        self,
        *,
        company_id: UUID,
        draft_id: UUID,
    ) -> DraftAggregate | None:
        draft = await self.get_draft(company_id=company_id, draft_id=draft_id)
        if draft is None:
            return None
        standard = await self.get_standard(company_id=company_id, standard_id=draft.standard_id)
        if standard is None:
            return None
        requirements = await self.list_requirements(
            company_id=company_id,
            standard_id=draft.standard_id,
            active_only=False,
        )
        claims = await self.list_claims(company_id=company_id, draft_id=draft_id)
        citations = await self.list_citations(
            company_id=company_id,
            claim_ids=[item.id for item in claims],
        )
        gaps = await self.list_gaps(company_id=company_id, draft_id=draft_id)
        bindings = await self.list_fact_bindings(company_id=company_id, draft_id=draft_id)
        measurement_id = _metadata_uuid(draft.validation_summary.get("measurement_id"))
        measurement = (
            await self.get_measurement(company_id=company_id, measurement_id=measurement_id)
            if measurement_id is not None
            else None
        )
        evidence_ids = {
            item.evidence_item_id for item in citations if item.evidence_item_id is not None
        } | {item.evidence_item_id for item in bindings if item.evidence_item_id is not None}
        evidence = await self._list_evidence_records(
            company_id=company_id,
            evidence_ids=evidence_ids,
        )
        event_ids = (
            {item.ledger_event_id for item in claims if item.ledger_event_id is not None}
            | {item.ledger_event_id for item in citations if item.ledger_event_id is not None}
            | {item.ledger_event_id for item in bindings}
        )
        if draft.ledger_event_id is not None:
            event_ids.add(draft.ledger_event_id)
        events = await self.list_ledger_events(company_id=company_id, event_ids=event_ids)
        approval = await self.get_generic_approval(
            company_id=company_id,
            target_id=draft_id,
        )
        return DraftAggregate(
            draft=draft,
            standard=standard,
            requirements=tuple(requirements),
            measurement=measurement,
            claims=tuple(claims),
            citations=tuple(citations),
            gaps=tuple(gaps),
            fact_bindings=tuple(bindings),
            evidence=tuple(evidence),
            ledger_events=tuple(events),
            approval=approval,
        )

    async def flush(self) -> None:
        await self.session.flush()

    async def _list_evidence_records(
        self,
        *,
        company_id: UUID,
        evidence_ids: Collection[UUID],
    ) -> list[EvidenceRecord]:
        if not evidence_ids:
            return []
        rows = (
            await self.session.execute(
                select(EvidenceItem, SourceDocument, DataSource)
                .join(
                    SourceDocument,
                    (SourceDocument.company_id == EvidenceItem.company_id)
                    & (SourceDocument.id == EvidenceItem.source_document_id),
                )
                .join(
                    DataSource,
                    (DataSource.company_id == SourceDocument.company_id)
                    & (DataSource.id == SourceDocument.data_source_id),
                )
                .where(
                    EvidenceItem.company_id == company_id,
                    EvidenceItem.id.in_(evidence_ids),
                )
                .order_by(
                    SourceDocument.checksum,
                    EvidenceItem.locator,
                    EvidenceItem.id,
                )
            )
        ).all()
        return [EvidenceRecord(row[0], row[1], row[2]) for row in rows]


def _metadata_uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None
