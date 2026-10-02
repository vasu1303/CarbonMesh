from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import CarbonMeasurement
from app.db.models.procurement import (
    Approval,
    FactBinding,
    ProcurementScenario,
    Recommendation,
    SupplierScore,
)
from app.db.models.semantic import MethodDefinition
from app.modules.approvals.service import ApprovalServiceError, create_pending_approval
from app.modules.ledger.service import (
    append_ledger_event,
    append_lineage_edge,
    attach_ledger_evidence,
    payload_sha256,
)
from app.modules.procurement.errors import ProcurementError, not_found
from app.modules.procurement.repository import (
    ProcurementRepository,
    ProductRecord,
    ScenarioDependencies,
    SupplierRecord,
)
from app.modules.procurement.review import (
    REVIEW_SNAPSHOT_VERSION,
    build_recommendation_payload,
    build_review_source_state,
    canonicalize_scenario_context,
    recommendation_preview_is_current,
    review_source_hash,
    scenario_context_signature,
    stored_recommendation_payload,
)
from app.modules.procurement.schemas import (
    ApprovalPreviewSummary,
    AssessmentRunRequest,
    AssessmentRunResult,
    BoundNarrative,
    CreateScenarioRequest,
    EvidenceDetail,
    EvidenceSummary,
    FactBindingView,
    MaterialConstraints,
    ProcurementScenarioResult,
    ProductAssessment,
    ProductImpact,
    RecommendationDetail,
    RecommendationSummary,
    ScenarioConstraints,
    ScoreComponents,
    ScoringMethod,
    ScoringWeights,
    SupplierList,
    SupplierProductDetail,
    SupplierProductList,
    SupplierProductSummary,
    SupplierSummary,
)
from app.modules.procurement.scoring import (
    CARBON_QUANTUM,
    SCORE_QUANTUM,
    CalculatedAssessment,
    ProductFacts,
    ScenarioFacts,
    calculate_assessment,
    infer_supplier_risk,
    quantize,
)

DEFAULT_WEIGHTS = {
    "carbon": Decimal("0.4000"),
    "evidence": Decimal("0.2500"),
    "circularity": Decimal("0.2000"),
    "operational_fit": Decimal("0.1500"),
}
RATIONALE_TEMPLATE = (
    "Switching from {fact_current_product} to {fact_recommended_product} "
    "could avoid {fact_avoided_kgco2e} while changing unit cost by "
    "{fact_cost_delta_pct}."
)
PLACEHOLDER_PATTERN = re.compile(r"\{(fact_[a-z0-9_]+)\}")


class ProcurementService:
    """Application service for deterministic procurement reads and commands."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = ProcurementRepository(session)

    async def list_suppliers(
        self,
        *,
        company_id: UUID,
        country_code: str | None,
        active_only: bool,
        search: str | None,
        limit: int,
        offset: int,
    ) -> SupplierList:
        try:
            rows, total = await self.repository.list_suppliers(
                company_id=company_id,
                country_code=country_code,
                active_only=active_only,
                search=search,
                limit=limit,
                offset=offset,
            )
        except SQLAlchemyError as error:
            raise self._database_unavailable() from error
        return SupplierList(
            items=[self._supplier_summary(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def list_supplier_products(
        self,
        *,
        company_id: UUID,
        supplier_id: UUID | None,
        material_code: str | None,
        category: str | None,
        active_only: bool,
        search: str | None,
        limit: int,
        offset: int,
    ) -> SupplierProductList:
        try:
            rows, total = await self.repository.list_products(
                company_id=company_id,
                supplier_id=supplier_id,
                material_code=material_code,
                category=category,
                active_only=active_only,
                search=search,
                limit=limit,
                offset=offset,
            )
        except SQLAlchemyError as error:
            raise self._database_unavailable() from error
        return SupplierProductList(
            items=[self._product_summary(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_supplier_product(
        self, *, company_id: UUID, product_id: UUID
    ) -> SupplierProductDetail:
        try:
            row = await self.repository.get_product(company_id=company_id, product_id=product_id)
        except SQLAlchemyError as error:
            raise self._database_unavailable() from error
        if row is None:
            raise not_found("supplier_product", product_id)
        evidence = row.evidence
        return SupplierProductDetail(
            **self._product_summary(row).model_dump(),
            company_id=row.product.company_id,
            supplier_metadata=row.supplier.supplier_metadata,
            evidence=(
                EvidenceDetail(
                    id=evidence.id,
                    source_document_id=evidence.source_document_id,
                    evidence_type=evidence.evidence_type,
                    locator=evidence.locator,
                    content_text=evidence.content_text,
                    checksum=evidence.checksum,
                    metadata=evidence.evidence_metadata,
                    created_at=evidence.created_at,
                    updated_at=evidence.updated_at,
                )
                if evidence is not None
                else None
            ),
            created_at=row.product.created_at,
            updated_at=row.product.updated_at,
        )

    async def create_scenario(self, request: CreateScenarioRequest) -> ProcurementScenarioResult:
        try:
            dependencies = await self.repository.get_scenario_dependencies(
                company_id=request.company_id,
                site_id=request.site_id,
                reporting_period_id=request.reporting_period_id,
                current_product_id=request.current_product_id,
                carbon_measurement_id=request.carbon_measurement_id,
                method_definition_id=request.method_definition_id,
                requested_by=request.requested_by,
            )
            if dependencies is None:
                raise ProcurementError(
                    code="invalid_scenario_context",
                    message=("One or more company-scoped scenario references do not exist."),
                    status_code=422,
                )
            self._validate_dependencies(request, dependencies)

            current_cost = (
                request.current_unit_cost
                if request.current_unit_cost is not None
                else dependencies.current_product.product.unit_cost
            )
            if not current_cost.is_finite() or current_cost <= 0:
                raise ProcurementError(
                    code="invalid_current_unit_cost",
                    message="Current unit cost must be a finite value greater than zero.",
                    status_code=422,
                    field_details=[
                        {
                            "field": "current_unit_cost",
                            "value": str(current_cost),
                        }
                    ],
                )
            currency = request.currency or dependencies.current_product.product.currency
            weights = self._resolve_weights(dependencies.method)
            frozen_context = self._frozen_context(
                request=request,
                current_product=dependencies.current_product,
                current_unit_cost=current_cost,
                currency=currency,
                weights=weights,
                method=dependencies.method,
            )
            normalized_context = canonicalize_scenario_context(frozen_context)
            signature = scenario_context_signature(normalized_context)

            existing = await self.repository.find_scenario_by_signature(
                company_id=request.company_id, analysis_signature=signature
            )
            if existing is not None:
                return await self._scenario_result(existing)

            scenario = ProcurementScenario(
                company_id=request.company_id,
                site_id=request.site_id,
                reporting_period_id=request.reporting_period_id,
                current_product_id=request.current_product_id,
                carbon_measurement_id=request.carbon_measurement_id,
                agent_run_id=request.agent_run_id,
                method_definition_id=request.method_definition_id,
                quantity=request.quantity,
                quantity_unit=request.quantity_unit,
                current_unit_cost=current_cost,
                currency=currency,
                max_cost_increase_pct=request.max_cost_increase_pct,
                max_lead_time_days=request.max_lead_time_days,
                minimum_circularity_score=request.minimum_circularity_score,
                material_constraints=request.material_constraints.model_dump(mode="json"),
                carbon_weight=weights.carbon,
                evidence_weight=weights.evidence,
                circularity_weight=weights.circularity,
                operational_fit_weight=weights.operational_fit,
                analysis_signature=signature,
                frozen_context=normalized_context,
                status="draft",
            )
            self.session.add(scenario)
            await self.session.flush()
            await self._assess_scenario(
                scenario,
                requested_by=request.requested_by,
                approval_expires_at=request.approval_expires_at,
                idempotency_key=request.idempotency_key,
            )
            await self.session.commit()
            await self.session.refresh(scenario)
            return await self._scenario_result(scenario)
        except ProcurementError:
            await self.session.rollback()
            raise
        except IntegrityError as error:
            await self.session.rollback()
            raise ProcurementError(
                code="scenario_conflict",
                message="The frozen scenario conflicts with an existing procurement run.",
                status_code=409,
                retryable=False,
            ) from error
        except SQLAlchemyError as error:
            await self.session.rollback()
            raise self._database_unavailable() from error

    async def run_assessment(self, request: AssessmentRunRequest) -> AssessmentRunResult:
        try:
            scenario = await self.repository.get_scenario(
                company_id=request.company_id, scenario_id=request.scenario_id
            )
            if scenario is None:
                raise not_found("procurement_scenario", request.scenario_id)
            actor_value = scenario.frozen_context.get("actor_id")
            requested_by = UUID(actor_value) if isinstance(actor_value, str) else None
            await self._assess_scenario(scenario, requested_by=requested_by)
            await self.session.commit()
            await self.session.refresh(scenario)
            return await self._assessment_result(scenario)
        except ProcurementError:
            await self.session.rollback()
            raise
        except IntegrityError as error:
            await self.session.rollback()
            raise ProcurementError(
                code="assessment_conflict",
                message="This scenario has already been assessed with conflicting state.",
                status_code=409,
            ) from error
        except (SQLAlchemyError, ValueError) as error:
            await self.session.rollback()
            if isinstance(error, ValueError):
                raise ProcurementError(
                    code="invalid_frozen_context",
                    message="The scenario has an invalid frozen actor identifier.",
                    status_code=422,
                ) from error
            raise self._database_unavailable() from error

    async def get_scenario(
        self, *, company_id: UUID, scenario_id: UUID
    ) -> ProcurementScenarioResult:
        try:
            scenario = await self.repository.get_scenario(
                company_id=company_id, scenario_id=scenario_id
            )
            if scenario is None:
                raise not_found("procurement_scenario", scenario_id)
            return await self._scenario_result(scenario)
        except ProcurementError:
            raise
        except SQLAlchemyError as error:
            raise self._database_unavailable() from error

    async def get_recommendation(
        self, *, company_id: UUID, recommendation_id: UUID
    ) -> RecommendationDetail:
        try:
            recommendation = await self.repository.get_recommendation(
                company_id=company_id, recommendation_id=recommendation_id
            )
            if recommendation is None:
                raise not_found("recommendation", recommendation_id)
            return await self._recommendation_detail(recommendation)
        except ProcurementError:
            raise
        except SQLAlchemyError as error:
            raise self._database_unavailable() from error

    async def get_scenario_recommendation(
        self, *, company_id: UUID, scenario_id: UUID
    ) -> RecommendationDetail:
        try:
            recommendation = await self.repository.get_recommendation_for_scenario(
                company_id=company_id,
                scenario_id=scenario_id,
            )
            if recommendation is None:
                scenario = await self.repository.get_scenario(
                    company_id=company_id,
                    scenario_id=scenario_id,
                )
                if scenario is None:
                    raise not_found("procurement_scenario", scenario_id)
                raise ProcurementError(
                    code="recommendation_not_found",
                    message="The procurement scenario does not have a recommendation.",
                    status_code=404,
                    field_details=[{"field": "scenario_id", "value": str(scenario_id)}],
                )
            return await self._recommendation_detail(recommendation)
        except ProcurementError:
            raise
        except SQLAlchemyError as error:
            raise self._database_unavailable() from error

    async def _recommendation_detail(
        self, recommendation: Recommendation
    ) -> RecommendationDetail:
        recommendation_id = recommendation.id
        company_id = recommendation.company_id
        try:
            if (
                payload_sha256(stored_recommendation_payload(recommendation))[1]
                != recommendation.payload_hash
            ):
                raise ProcurementError(
                    code="recommendation_integrity_error",
                    message="The stored recommendation no longer matches its payload hash.",
                    status_code=500,
                )
            review = recommendation.impact_snapshot.get("review")
            if not isinstance(review, dict):
                raise ProcurementError(
                    code="recommendation_integrity_error",
                    message="The recommendation is missing its frozen review payload.",
                    status_code=500,
                )
            try:
                baseline_product = SupplierProductSummary.model_validate(review["baseline_product"])
                recommended_product = SupplierProductSummary.model_validate(
                    review["recommended_product"]
                )
                assessment = ProductAssessment.model_validate(review["supplier_score"])
                evidence = [EvidenceSummary.model_validate(item) for item in review["evidence"]]
                binding_snapshots = review["fact_bindings"]
                if not isinstance(binding_snapshots, list):
                    raise TypeError("fact_bindings must be a list")
            except (KeyError, TypeError, ValueError) as error:
                raise ProcurementError(
                    code="recommendation_integrity_error",
                    message="The frozen recommendation review payload is invalid.",
                    status_code=500,
                ) from error
            bindings = await self.repository.list_fact_bindings(
                company_id=company_id, recommendation_id=recommendation_id
            )
            narrative = self._bound_narrative(
                recommendation=recommendation,
                binding_snapshots=binding_snapshots,
                persisted_bindings=bindings,
            )
            approval = await self.repository.get_approval(
                company_id=company_id, recommendation_id=recommendation_id
            )
            return RecommendationDetail(
                id=recommendation.id,
                company_id=recommendation.company_id,
                scenario_id=recommendation.scenario_id,
                status=recommendation.status,
                baseline_product=baseline_product,
                recommended_product=recommended_product,
                supplier_score=assessment,
                evidence=evidence,
                projected_footprint_kgco2e=assessment.impact.projected_footprint_kgco2e,
                avoided_kgco2e=assessment.impact.avoided_kgco2e,
                reduction_pct=assessment.impact.reduction_pct,
                cost_delta_pct=assessment.impact.cost_delta_pct,
                lead_time_delta_days=assessment.impact.lead_time_delta_days,
                narrative=narrative,
                analysis_signature=recommendation.analysis_signature,
                payload_hash=recommendation.payload_hash,
                impact_snapshot=recommendation.impact_snapshot,
                ledger_event_id=recommendation.ledger_event_id,
                approval=self._approval_summary(approval),
                invalidated_at=recommendation.invalidated_at,
                created_at=recommendation.created_at,
            )
        except ProcurementError:
            raise
        except SQLAlchemyError as error:
            raise self._database_unavailable() from error

    async def is_recommendation_preview_current(
        self,
        *,
        company_id: UUID,
        recommendation_id: UUID,
    ) -> bool:
        """Revalidate every upstream input before exposing an approval preview."""

        try:
            recommendation = await self.repository.get_recommendation(
                company_id=company_id,
                recommendation_id=recommendation_id,
            )
            if recommendation is None:
                raise not_found("recommendation", recommendation_id)
            return await recommendation_preview_is_current(self.session, recommendation)
        except ProcurementError:
            raise
        except SQLAlchemyError as error:
            raise self._database_unavailable() from error

    async def _assess_scenario(
        self,
        scenario: ProcurementScenario,
        *,
        requested_by: UUID | None,
        approval_expires_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        existing_scores = await self.repository.list_scores(
            company_id=scenario.company_id, scenario_id=scenario.id
        )
        if existing_scores:
            return

        current = await self.repository.get_product(
            company_id=scenario.company_id,
            product_id=scenario.current_product_id,
        )
        period = await self.repository.get_period(
            company_id=scenario.company_id,
            reporting_period_id=scenario.reporting_period_id,
        )
        method = await self.repository.get_method(
            company_id=scenario.company_id,
            method_id=scenario.method_definition_id,
        )
        measurement = await self.repository.get_measurement(
            company_id=scenario.company_id,
            measurement_id=scenario.carbon_measurement_id,
        )
        if current is None or period is None or method is None or measurement is None:
            raise ProcurementError(
                code="scenario_integrity_error",
                message="The scenario references unavailable frozen context.",
                status_code=500,
            )
        candidates = await self.repository.list_candidate_products(
            company_id=scenario.company_id,
            current_product_id=scenario.current_product_id,
            category=current.product.category,
        )
        scenario_facts = self._scenario_facts(scenario, period.start_date, period.end_date)
        current_facts = self._product_facts(current)
        calculations = [
            (
                candidate,
                calculate_assessment(
                    current=current_facts,
                    candidate=self._product_facts(candidate),
                    scenario=scenario_facts,
                ),
            )
            for candidate in candidates
        ]
        feasible_order = sorted(
            (item for item in calculations if item[1].feasible),
            key=lambda item: (
                -item[1].total_score,
                item[1].projected_footprint_kgco2e,
                item[0].product.unit_cost,
                str(item[0].product.id),
            ),
        )
        ranks = {item[0].product.id: index for index, item in enumerate(feasible_order, 1)}
        persisted: dict[UUID, SupplierScore] = {}
        for candidate, calculated in calculations:
            score = SupplierScore(
                company_id=scenario.company_id,
                scenario_id=scenario.id,
                supplier_product_id=candidate.product.id,
                method_definition_id=scenario.method_definition_id,
                agent_run_id=scenario.agent_run_id,
                ledger_event_id=None,
                carbon_score=calculated.carbon_score,
                evidence_score=calculated.evidence_score,
                circularity_score=calculated.circularity_score,
                operational_fit_score=calculated.operational_fit_score,
                total_score=calculated.total_score,
                rank=ranks.get(candidate.product.id),
                feasible=calculated.feasible,
                infeasibility_reasons=calculated.infeasibility_reasons,
            )
            self.session.add(score)
            persisted[candidate.product.id] = score
        await self.session.flush()
        # Canonicalize fixed-precision NUMERIC values before building the review fingerprint.
        await self.session.refresh(scenario)
        for score in persisted.values():
            await self.session.refresh(score)

        assessments_by_product = {
            candidate.product.id: self._assessment_view(
                persisted[candidate.product.id], candidate, calculated
            )
            for candidate, calculated in calculations
        }
        assessment_snapshot = list(assessments_by_product.values())
        assessment_snapshot.sort(
            key=lambda item: (
                item.rank is None,
                item.rank or 0,
                -item.scores.total,
                str(item.product.id),
            )
        )
        scenario.frozen_context = {
            **scenario.frozen_context,
            "assessment_snapshot": [item.model_dump(mode="json") for item in assessment_snapshot],
        }
        await self.session.flush()

        if not feasible_order:
            scenario.status = "assessed"
            await self.session.flush()
            return

        selected_record, selected_calculation = feasible_order[0]
        selected_score = persisted[selected_record.product.id]
        await self._create_recommendation(
            scenario=scenario,
            current=current,
            selected=selected_record,
            score=selected_score,
            assessment=assessments_by_product[selected_record.product.id],
            calculated=selected_calculation,
            method=method,
            requested_by=requested_by,
            approval_expires_at=approval_expires_at,
            idempotency_key=idempotency_key,
            measurement=measurement,
            eligible_candidates=candidates,
            candidate_assessments=[
                (persisted[candidate.product.id], candidate) for candidate, _ in calculations
            ],
        )
        scenario.status = "recommended"
        await self.session.flush()

    async def _create_recommendation(
        self,
        *,
        scenario: ProcurementScenario,
        current: ProductRecord,
        selected: ProductRecord,
        score: SupplierScore,
        assessment: ProductAssessment,
        calculated: CalculatedAssessment,
        method: MethodDefinition,
        requested_by: UUID | None,
        approval_expires_at: datetime | None,
        idempotency_key: str | None,
        measurement: CarbonMeasurement,
        eligible_candidates: list[ProductRecord],
        candidate_assessments: list[tuple[SupplierScore, ProductRecord]],
    ) -> None:
        evidence_ids = [
            str(value)
            for value in (current.product.evidence_item_id, selected.product.evidence_item_id)
            if value is not None
        ]
        fact_bindings = self._fact_bindings(
            current=current,
            selected=selected,
            score=score,
            calculated=calculated,
        )
        fact_snapshots = [binding.model_dump(mode="json") for binding in fact_bindings]
        review_snapshot = {
            "schema_version": REVIEW_SNAPSHOT_VERSION,
            "baseline_product": self._product_summary(current).model_dump(mode="json"),
            "recommended_product": self._product_summary(selected).model_dump(mode="json"),
            "supplier_score": assessment.model_dump(mode="json"),
            "evidence": [
                evidence.model_dump(mode="json")
                for evidence in self._evidence_summaries(current, selected)
            ],
            "fact_bindings": fact_snapshots,
        }
        source_state_hash = review_source_hash(
            build_review_source_state(
                scenario=scenario,
                baseline=current,
                selected=selected,
                score=score,
                method=method,
                measurement=measurement,
                candidate_assessments=candidate_assessments,
                eligible_candidates=eligible_candidates,
            )
        )
        impact_snapshot = {
            "schema_version": REVIEW_SNAPSHOT_VERSION,
            "source_state_hash": source_state_hash,
            "review": review_snapshot,
            "baseline_footprint_kgco2e": str(
                quantize(scenario.quantity * current.product.pcf_kgco2e_per_unit, CARBON_QUANTUM)
            ),
            "projected_footprint_kgco2e": str(calculated.projected_footprint_kgco2e),
            "avoided_kgco2e": str(calculated.avoided_kgco2e),
            "reduction_pct": str(calculated.reduction_pct),
            "cost_delta_pct": str(calculated.cost_delta_pct),
            "lead_time_delta_days": calculated.lead_time_delta_days,
            "quantity": str(scenario.quantity),
            "quantity_unit": scenario.quantity_unit,
            "current_product": {
                "id": str(current.product.id),
                "name": current.product.name,
                "pcf": str(current.product.pcf_kgco2e_per_unit),
            },
            "recommended_product": {
                "id": str(selected.product.id),
                "name": selected.product.name,
                "pcf": str(selected.product.pcf_kgco2e_per_unit),
            },
            "score": {
                "id": str(score.id),
                "total": str(score.total_score),
                "method_id": str(method.id),
                "method_key": method.key,
                "method_version": method.version,
            },
            "evidence_item_ids": evidence_ids,
        }
        payload = build_recommendation_payload(
            company_id=scenario.company_id,
            scenario_id=scenario.id,
            baseline_product_id=current.product.id,
            recommended_product_id=selected.product.id,
            supplier_score_id=score.id,
            analysis_signature=scenario.analysis_signature,
            projected_footprint_kgco2e=calculated.projected_footprint_kgco2e,
            avoided_kgco2e=calculated.avoided_kgco2e,
            reduction_pct=calculated.reduction_pct,
            cost_delta_pct=calculated.cost_delta_pct,
            lead_time_delta_days=calculated.lead_time_delta_days,
            rationale_template=RATIONALE_TEMPLATE,
            impact_snapshot=impact_snapshot,
        )
        normalized_payload, payload_hash = payload_sha256(payload)
        recommendation = Recommendation(
            company_id=scenario.company_id,
            scenario_id=scenario.id,
            recommended_product_id=selected.product.id,
            baseline_product_id=current.product.id,
            supplier_score_id=score.id,
            ledger_event_id=None,
            status="pending_approval",
            projected_footprint_kgco2e=calculated.projected_footprint_kgco2e,
            avoided_kgco2e=calculated.avoided_kgco2e,
            reduction_pct=calculated.reduction_pct,
            cost_delta_pct=calculated.cost_delta_pct,
            lead_time_delta_days=calculated.lead_time_delta_days,
            rationale_template=RATIONALE_TEMPLATE,
            analysis_signature=scenario.analysis_signature,
            payload_hash=payload_hash,
            impact_snapshot=impact_snapshot,
            invalidated_at=None,
        )
        self.session.add(recommendation)
        await self.session.flush()
        ledger_event = await append_ledger_event(
            self.session,
            company_id=scenario.company_id,
            event_type="recommendation.created",
            entity_type="recommendation",
            entity_id=recommendation.id,
            payload=normalized_payload,
            analysis_signature=scenario.analysis_signature,
            created_by=requested_by,
        )
        recommendation.ledger_event_id = ledger_event.id
        score.ledger_event_id = ledger_event.id
        await self.session.flush()

        if measurement.ledger_event_id is not None:
            await append_lineage_edge(
                self.session,
                company_id=scenario.company_id,
                parent_event_id=measurement.ledger_event_id,
                child_event_id=ledger_event.id,
                relationship_type="informed_recommendation",
                metadata={"scenario_id": scenario.id},
            )
        evidence_links = (
            (current.product.evidence_item_id, "baseline"),
            (selected.product.evidence_item_id, "recommended"),
        )
        attached: set[UUID] = set()
        for evidence_item_id, relevance in evidence_links:
            if evidence_item_id is None or evidence_item_id in attached:
                continue
            await attach_ledger_evidence(
                self.session,
                company_id=scenario.company_id,
                ledger_event_id=ledger_event.id,
                evidence_item_id=evidence_item_id,
                relevance=relevance,
            )
            attached.add(evidence_item_id)

        for binding in fact_bindings:
            binding_payload = {
                "artifact_type": "procurement_recommendation",
                "artifact_id": recommendation.id,
                "placeholder": binding.placeholder,
                "ledger_event_id": ledger_event.id,
                "evidence_item_id": binding.evidence_item_id,
                "value_snapshot": binding.value,
                "display_value": binding.display_value,
                "unit": binding.unit,
                "context_hash": scenario.analysis_signature,
            }
            self.session.add(
                FactBinding(
                    company_id=scenario.company_id,
                    artifact_type="procurement_recommendation",
                    artifact_id=recommendation.id,
                    recommendation_id=recommendation.id,
                    agent_run_id=scenario.agent_run_id,
                    ledger_event_id=ledger_event.id,
                    evidence_item_id=binding.evidence_item_id,
                    placeholder=binding.placeholder,
                    value_snapshot=binding.value,
                    display_value=binding.display_value,
                    unit=binding.unit,
                    context_hash=scenario.analysis_signature,
                    binding_hash=payload_sha256(binding_payload)[1],
                )
            )
        await self.session.flush()

        if requested_by is not None:
            try:
                await create_pending_approval(
                    self.session,
                    recommendation=recommendation,
                    requested_by=requested_by,
                    expires_at=approval_expires_at,
                    idempotency_key=idempotency_key,
                )
            except ApprovalServiceError as error:
                raise ProcurementError(
                    code=error.code,
                    message=error.message,
                    status_code=error.status_code,
                    retryable=error.retryable,
                    field_details=[
                        {"field": key, "detail": value}
                        for key, value in error.field_details.items()
                    ],
                ) from error

    async def _scenario_result(self, scenario: ProcurementScenario) -> ProcurementScenarioResult:
        frozen = scenario.frozen_context
        if scenario_context_signature(frozen) != scenario.analysis_signature:
            raise ProcurementError(
                code="scenario_integrity_error",
                message="The frozen scenario context no longer matches its signature.",
                status_code=500,
            )
        try:
            current_product = SupplierProductSummary.model_validate(
                frozen["current_product_display"]
            )
            method = ScoringMethod.model_validate(frozen["scoring_method"])
            alternatives = [
                ProductAssessment.model_validate(item) for item in frozen["assessment_snapshot"]
            ]
            frozen_constraints = frozen["constraints"]
            constraints = ScenarioConstraints(
                max_cost_increase_pct=frozen_constraints["max_cost_increase_pct"],
                max_lead_time_days=frozen_constraints["max_lead_time_days"],
                minimum_circularity_score=frozen_constraints["minimum_circularity_score"],
                material=MaterialConstraints.model_validate(frozen_constraints["material"]),
            )
            frozen_current = frozen["current_product"]
        except (KeyError, TypeError, ValueError) as error:
            raise ProcurementError(
                code="scenario_integrity_error",
                message="The scenario is missing its frozen comparison matrix.",
                status_code=500,
            ) from error
        recommendation = await self.repository.get_recommendation_for_scenario(
            company_id=scenario.company_id, scenario_id=scenario.id
        )
        approval = (
            await self.repository.get_approval(
                company_id=scenario.company_id,
                recommendation_id=recommendation.id,
            )
            if recommendation is not None
            else None
        )
        return ProcurementScenarioResult(
            id=scenario.id,
            company_id=scenario.company_id,
            site_id=frozen["site_id"],
            reporting_period_id=frozen["reporting_period_id"],
            current_product=current_product,
            carbon_measurement_id=frozen["carbon_measurement_id"],
            agent_run_id=frozen["agent_run_id"],
            method=method,
            quantity=frozen["quantity"],
            quantity_unit=frozen["quantity_unit"],
            current_unit_cost=frozen_current["unit_cost"],
            currency=frozen_current["currency"],
            constraints=constraints,
            weights=method.weights,
            analysis_signature=scenario.analysis_signature,
            frozen_context=scenario.frozen_context,
            status=scenario.status,
            terminal_state="completed" if recommendation is not None else "no_feasible_option",
            alternatives=alternatives,
            selected_recommendation=(
                self._recommendation_summary(recommendation, approval)
                if recommendation is not None
                else None
            ),
            created_at=scenario.created_at,
            updated_at=scenario.updated_at,
        )

    async def _assessment_result(self, scenario: ProcurementScenario) -> AssessmentRunResult:
        result = await self._scenario_result(scenario)
        return AssessmentRunResult(
            scenario_id=result.id,
            method=result.method,
            terminal_state=result.terminal_state,
            assessments=result.alternatives,
            selected_product_id=(
                result.selected_recommendation.recommended_product_id
                if result.selected_recommendation is not None
                else None
            ),
            recommendation_id=(
                result.selected_recommendation.id
                if result.selected_recommendation is not None
                else None
            ),
        )

    def _validate_dependencies(
        self, request: CreateScenarioRequest, dependencies: ScenarioDependencies
    ) -> None:
        current = dependencies.current_product.product
        supplier = dependencies.current_product.supplier
        measurement = dependencies.measurement
        method = dependencies.method
        errors: list[dict[str, Any]] = []
        if not dependencies.site.is_active:
            errors.append({"field": "site_id", "code": "inactive_site"})
        if not current.is_active or supplier.status != "active":
            errors.append({"field": "current_product_id", "code": "inactive_product"})
        if current.effective_from > dependencies.period.end_date or (
            current.effective_to is not None
            and current.effective_to < dependencies.period.start_date
        ):
            errors.append({"field": "current_product_id", "code": "outside_reporting_period"})
        if current.evidence_item_id is None:
            errors.append({"field": "current_product_id", "code": "missing_evidence"})
        if measurement.status != "verified":
            errors.append({"field": "carbon_measurement_id", "code": "not_verified"})
        if measurement.site_id != request.site_id:
            errors.append({"field": "carbon_measurement_id", "code": "site_mismatch"})
        if measurement.reporting_period_id != request.reporting_period_id:
            errors.append({"field": "carbon_measurement_id", "code": "period_mismatch"})
        if method.method_type != "supplier_scoring" or not method.is_active:
            errors.append({"field": "method_definition_id", "code": "invalid_scoring_method"})
        if method.effective_from > dependencies.period.end_date or (
            method.effective_to is not None and method.effective_to < dependencies.period.start_date
        ):
            errors.append({"field": "method_definition_id", "code": "outside_reporting_period"})
        if not dependencies.requested_by.is_active:
            errors.append({"field": "requested_by", "code": "inactive_actor"})
        denominator = current.pcf_unit.rsplit("/", 1)[-1].strip().lower()
        if denominator != request.quantity_unit.strip().lower():
            errors.append(
                {
                    "field": "quantity_unit",
                    "code": "pcf_unit_mismatch",
                    "expected": denominator,
                }
            )
        expected_baseline = quantize(request.quantity * current.pcf_kgco2e_per_unit, CARBON_QUANTUM)
        if expected_baseline != quantize(measurement.value_kgco2e, CARBON_QUANTUM):
            errors.append(
                {
                    "field": "carbon_measurement_id",
                    "code": "measurement_value_mismatch",
                    "expected": str(expected_baseline),
                    "actual": str(measurement.value_kgco2e),
                }
            )
        if request.currency is not None and request.currency != current.currency:
            errors.append(
                {
                    "field": "currency",
                    "code": "current_product_currency_mismatch",
                    "expected": current.currency,
                }
            )
        if request.approval_expires_at is not None:
            expiry = request.approval_expires_at
            if expiry.tzinfo is None or expiry <= datetime.now(UTC):
                errors.append(
                    {"field": "approval_expires_at", "code": "must_be_future_utc_timestamp"}
                )
        if errors:
            raise ProcurementError(
                code="invalid_scenario_context",
                message="The scenario context failed deterministic validation.",
                status_code=422,
                field_details=errors,
            )

    def _resolve_weights(self, method: MethodDefinition) -> ScoringWeights:
        raw = method.configuration.get("weights", method.configuration)
        if not isinstance(raw, dict):
            raw = {}

        def read(name: str) -> Decimal:
            value = raw.get(name, raw.get(f"{name}_weight", DEFAULT_WEIGHTS[name]))
            try:
                return quantize(Decimal(str(value)), SCORE_QUANTUM)
            except (InvalidOperation, TypeError, ValueError) as error:
                raise ProcurementError(
                    code="invalid_scoring_method",
                    message="The scoring method contains a non-decimal weight.",
                    status_code=422,
                    field_details=[{"field": f"configuration.weights.{name}"}],
                ) from error

        weights = ScoringWeights(
            carbon=read("carbon"),
            evidence=read("evidence"),
            circularity=read("circularity"),
            operational_fit=read("operational_fit"),
        )
        values = [weights.carbon, weights.evidence, weights.circularity, weights.operational_fit]
        if any(value < 0 for value in values) or sum(values) != Decimal("1.0000"):
            raise ProcurementError(
                code="invalid_scoring_method",
                message="Scoring weights must be non-negative and sum to exactly one.",
                status_code=422,
            )
        return weights

    def _frozen_context(
        self,
        *,
        request: CreateScenarioRequest,
        current_product: ProductRecord,
        current_unit_cost: Decimal,
        currency: str,
        weights: ScoringWeights,
        method: MethodDefinition,
    ) -> dict[str, Any]:
        product = current_product.product
        return {
            "schema_version": "procurement-scenario.initial",
            "company_id": request.company_id,
            "site_id": request.site_id,
            "reporting_period_id": request.reporting_period_id,
            "carbon_measurement_id": request.carbon_measurement_id,
            "agent_run_id": request.agent_run_id,
            "actor_id": request.requested_by,
            "current_product": {
                "id": product.id,
                "supplier_id": product.supplier_id,
                "material_code": product.material_code,
                "category": product.category,
                "pcf_kgco2e_per_unit": product.pcf_kgco2e_per_unit,
                "pcf_unit": product.pcf_unit,
                "unit_cost": current_unit_cost,
                "currency": currency,
                "lead_time_days": product.lead_time_days,
                "evidence_item_id": product.evidence_item_id,
            },
            "current_product_display": self._product_summary(current_product).model_dump(
                mode="json"
            ),
            "quantity": request.quantity,
            "quantity_unit": request.quantity_unit,
            "constraints": {
                "max_cost_increase_pct": request.max_cost_increase_pct,
                "max_lead_time_days": request.max_lead_time_days,
                "minimum_circularity_score": request.minimum_circularity_score,
                "material": request.material_constraints.model_dump(mode="json"),
            },
            "scoring_method": {
                "id": method.id,
                "key": method.key,
                "version": method.version,
                "code_version": method.code_version,
                "weights": weights.model_dump(mode="json"),
            },
        }

    def _product_summary(self, record: ProductRecord) -> SupplierProductSummary:
        product = record.product
        supplier = record.supplier
        return SupplierProductSummary(
            id=product.id,
            supplier_id=supplier.id,
            supplier_code=supplier.supplier_code,
            supplier_name=supplier.name,
            supplier_country_code=supplier.country_code,
            supplier_status=supplier.status,
            risk=infer_supplier_risk(supplier.supplier_metadata),
            product_code=product.product_code,
            name=product.name,
            material_code=product.material_code,
            category=product.category,
            description=product.description,
            pcf_kgco2e_per_unit=product.pcf_kgco2e_per_unit,
            pcf_unit=product.pcf_unit,
            circularity_score=product.circularity_score,
            recycled_content_pct=product.recycled_content_pct,
            recyclable_pct=product.recyclable_pct,
            evidence_quality_score=product.evidence_quality_score,
            lead_time_days=product.lead_time_days,
            unit_cost=product.unit_cost,
            currency=product.currency,
            effective_from=product.effective_from,
            effective_to=product.effective_to,
            is_active=product.is_active,
            evidence_item_id=product.evidence_item_id,
            evidence_available=product.evidence_item_id is not None,
        )

    def _supplier_summary(self, record: SupplierRecord) -> SupplierSummary:
        supplier = record.supplier
        return SupplierSummary(
            id=supplier.id,
            supplier_code=supplier.supplier_code,
            name=supplier.name,
            country_code=supplier.country_code,
            status=supplier.status,
            risk=infer_supplier_risk(supplier.supplier_metadata),
            metadata=supplier.supplier_metadata,
            product_count=record.product_count,
            active_product_count=record.active_product_count,
            created_at=supplier.created_at,
            updated_at=supplier.updated_at,
        )

    def _product_facts(self, record: ProductRecord) -> ProductFacts:
        product = record.product
        return ProductFacts(
            material_code=product.material_code,
            category=product.category,
            pcf_kgco2e_per_unit=product.pcf_kgco2e_per_unit,
            pcf_unit=product.pcf_unit,
            circularity_score=product.circularity_score,
            evidence_quality_score=product.evidence_quality_score,
            lead_time_days=product.lead_time_days,
            unit_cost=product.unit_cost,
            currency=product.currency,
            effective_from=product.effective_from,
            effective_to=product.effective_to,
            evidence_item_id=product.evidence_item_id,
            supplier_risk=infer_supplier_risk(record.supplier.supplier_metadata),
        )

    def _evidence_summaries(self, *records: ProductRecord) -> list[EvidenceSummary]:
        seen: set[UUID] = set()
        result: list[EvidenceSummary] = []
        for record in records:
            evidence = record.evidence
            if evidence is None or evidence.id in seen:
                continue
            seen.add(evidence.id)
            result.append(
                EvidenceSummary(
                    id=evidence.id,
                    evidence_type=evidence.evidence_type,
                    locator=evidence.locator,
                    checksum=evidence.checksum,
                    metadata=evidence.evidence_metadata,
                )
            )
        return result

    def _scenario_facts(
        self, scenario: ProcurementScenario, period_start: date, period_end: date
    ) -> ScenarioFacts:
        return ScenarioFacts(
            quantity=scenario.quantity,
            current_unit_cost=scenario.current_unit_cost,
            currency=scenario.currency,
            max_cost_increase_pct=scenario.max_cost_increase_pct,
            max_lead_time_days=scenario.max_lead_time_days,
            minimum_circularity_score=scenario.minimum_circularity_score,
            material_constraints=MaterialConstraints.model_validate(scenario.material_constraints),
            period_start=period_start,
            period_end=period_end,
            carbon_weight=scenario.carbon_weight,
            evidence_weight=scenario.evidence_weight,
            circularity_weight=scenario.circularity_weight,
            operational_fit_weight=scenario.operational_fit_weight,
        )

    def _assessment_view(
        self,
        score: SupplierScore,
        product: ProductRecord,
        calculated: CalculatedAssessment,
    ) -> ProductAssessment:
        return ProductAssessment(
            score_id=score.id,
            product=self._product_summary(product),
            scores=ScoreComponents(
                carbon=score.carbon_score,
                evidence=score.evidence_score,
                circularity=score.circularity_score,
                operational_fit=score.operational_fit_score,
                total=score.total_score,
            ),
            feasible=score.feasible,
            infeasibility_reasons=score.infeasibility_reasons,
            rank=score.rank,
            impact=ProductImpact(
                projected_footprint_kgco2e=calculated.projected_footprint_kgco2e,
                avoided_kgco2e=calculated.avoided_kgco2e,
                reduction_pct=calculated.reduction_pct,
                cost_delta_pct=calculated.cost_delta_pct,
                lead_time_delta_days=calculated.lead_time_delta_days,
            ),
        )

    def _recommendation_summary(
        self, recommendation: Recommendation, approval: Approval | None
    ) -> RecommendationSummary:
        return RecommendationSummary(
            id=recommendation.id,
            status=recommendation.status,
            recommended_product_id=recommendation.recommended_product_id,
            payload_hash=recommendation.payload_hash,
            analysis_signature=recommendation.analysis_signature,
            impact=ProductImpact(
                projected_footprint_kgco2e=recommendation.projected_footprint_kgco2e,
                avoided_kgco2e=recommendation.avoided_kgco2e,
                reduction_pct=recommendation.reduction_pct,
                cost_delta_pct=recommendation.cost_delta_pct,
                lead_time_delta_days=recommendation.lead_time_delta_days,
            ),
            approval=self._approval_summary(approval),
        )

    def _approval_summary(self, approval: Approval | None) -> ApprovalPreviewSummary | None:
        if approval is None:
            return None
        return ApprovalPreviewSummary(
            id=approval.id,
            status=approval.status,
            preview_hash=approval.preview_hash,
            analysis_signature=approval.analysis_signature,
            expires_at=approval.expires_at,
        )

    def _bound_narrative(
        self,
        *,
        recommendation: Recommendation,
        binding_snapshots: list[dict[str, Any]],
        persisted_bindings: list[FactBinding],
    ) -> BoundNarrative:
        relational = {binding.placeholder: binding for binding in persisted_bindings}
        views: list[FactBindingView] = []
        for snapshot in binding_snapshots:
            frozen = FactBindingView.model_validate(snapshot)
            persisted = relational.get(frozen.placeholder)
            views.append(
                FactBindingView(
                    id=persisted.id if persisted is not None else None,
                    placeholder=frozen.placeholder,
                    value=frozen.value,
                    display_value=frozen.display_value,
                    unit=frozen.unit,
                    ledger_event_id=(
                        persisted.ledger_event_id
                        if persisted is not None
                        else recommendation.ledger_event_id
                    ),
                    evidence_item_id=frozen.evidence_item_id,
                )
            )
        mapping = {binding.placeholder: binding.display_value for binding in views}
        unsupported = [
            placeholder
            for placeholder in PLACEHOLDER_PATTERN.findall(recommendation.rationale_template)
            if placeholder not in mapping
        ]
        resolved = recommendation.rationale_template
        for placeholder, value in mapping.items():
            resolved = resolved.replace(f"{{{placeholder}}}", value)
        evidence_links = sorted(
            {binding.evidence_item_id for binding in views if binding.evidence_item_id is not None},
            key=str,
        )
        return BoundNarrative(
            template_id="procurement-recommendation.initial",
            template=recommendation.rationale_template,
            resolved_text=resolved,
            fact_bindings=views,
            evidence_links=evidence_links,
            unsupported_fragments=unsupported,
        )

    def _fact_bindings(
        self,
        *,
        current: ProductRecord,
        selected: ProductRecord,
        score: SupplierScore,
        calculated: CalculatedAssessment,
    ) -> list[FactBindingView]:
        values = (
            (
                "fact_current_product",
                {"product_id": str(current.product.id), "name": current.product.name},
                current.product.name,
                None,
                current.product.evidence_item_id,
            ),
            (
                "fact_recommended_product",
                {"product_id": str(selected.product.id), "name": selected.product.name},
                selected.product.name,
                None,
                selected.product.evidence_item_id,
            ),
            (
                "fact_avoided_kgco2e",
                {"value": str(calculated.avoided_kgco2e), "unit": "kgCO2e"},
                f"{calculated.avoided_kgco2e} kgCO2e",
                "kgCO2e",
                selected.product.evidence_item_id,
            ),
            (
                "fact_cost_delta_pct",
                {"value": str(calculated.cost_delta_pct), "unit": "percent"},
                f"{calculated.cost_delta_pct}%",
                "percent",
                selected.product.evidence_item_id,
            ),
            (
                "fact_projected_footprint_kgco2e",
                {
                    "value": str(calculated.projected_footprint_kgco2e),
                    "unit": "kgCO2e",
                },
                f"{calculated.projected_footprint_kgco2e} kgCO2e",
                "kgCO2e",
                selected.product.evidence_item_id,
            ),
            (
                "fact_reduction_pct",
                {"value": str(calculated.reduction_pct), "unit": "percent"},
                f"{calculated.reduction_pct}%",
                "percent",
                selected.product.evidence_item_id,
            ),
            (
                "fact_total_score",
                {"value": str(score.total_score), "unit": "score"},
                str(score.total_score),
                "score",
                selected.product.evidence_item_id,
            ),
        )
        return [
            FactBindingView(
                placeholder=placeholder,
                value=value,
                display_value=display,
                unit=unit,
                evidence_item_id=evidence_item_id,
            )
            for placeholder, value, display, unit, evidence_item_id in values
        ]

    @staticmethod
    def _database_unavailable() -> ProcurementError:
        return ProcurementError(
            code="database_unavailable",
            message="Procurement data is temporarily unavailable.",
            status_code=503,
            retryable=True,
        )
