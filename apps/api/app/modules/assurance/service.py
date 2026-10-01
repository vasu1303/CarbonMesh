"""Deterministic Assurance application service.

The service treats source text as untrusted evidence.  It ranks only tenant- and
context-eligible evidence, binds numerical values to verified ledger facts, and
never lets retrieved prose choose identifiers or approval state.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.assurance import DisclosureRequirement
from app.db.models.core import Approval, AuditLog
from app.db.models.ledger import FactBinding
from app.modules.approvals.schemas import GenericApprovalPreviewRequest
from app.modules.approvals.service import (
    ApprovalServiceError,
    create_generic_approval_preview,
)
from app.modules.assurance.errors import (
    AssuranceConflictError,
    AssuranceNotFoundError,
    AssuranceStaleError,
    AssuranceValidationError,
)
from app.modules.assurance.repository import (
    AssuranceRepository,
    DraftAggregate,
    DraftDependencies,
    EvidenceRecord,
)
from app.modules.assurance.retrieval import (
    DEFAULT_MIN_EVIDENCE_SIMILARITY,
    EvidenceCandidate,
    EvidenceRetrievalContext,
    rank_evidence_candidates,
)
from app.modules.assurance.schemas import (
    ApprovalPreviewSummary,
    AssuranceAgentResult,
    AssuranceEvidencePack,
    AssuranceRequirementView,
    AssuranceStandardDetail,
    AssuranceStandardListResponse,
    AssuranceStandardSummary,
    ClaimCitationView,
    DisclosureClaimView,
    DisclosureDraftCreateRequest,
    DisclosureDraftValidateRequest,
    DisclosureDraftValidationResult,
    DisclosureDraftView,
    EvidenceGapView,
    FactBindingView,
    SafeEvidenceSummary,
)
from app.modules.ledger.service import (
    append_ledger_event,
    append_lineage_edge,
    attach_ledger_evidence,
    normalize_json,
    payload_sha256,
)
from app.modules.sources.embedding import EMBEDDING_MODEL_ID, hash_embedding

VALIDATION_METHOD_ID = "assurance-claim-validation-v1"
RETRIEVAL_METHOD_ID = "assurance-filtered-retrieval-v1"
APPROVAL_TTL = timedelta(hours=24)
DISCLAIMER = "POC draft; not an assurance opinion or filing."
MAX_EVIDENCE_CANDIDATES = 100
MAX_EVIDENCE_PER_CLAIM = 5
MAX_DRAFT_TITLE_LENGTH = 255
EVIDENCE_SIMILARITY_RULE = "minimum_similarity"
_PLACEHOLDER = re.compile(r"\{([a-zA-Z][a-zA-Z0-9_]*)\}")


@dataclass(frozen=True, slots=True)
class _PlannedGap:
    code: str
    severity: str
    message: str
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _PlannedClaim:
    requirement: DisclosureRequirement
    claim_type: str
    rendered_text: str | None
    support_status: str
    confidence: Decimal
    validation_details: dict[str, Any]
    evidence: tuple[EvidenceRecord, ...]
    fact_placeholder: str | None = None
    fact_display_value: str | None = None
    fact_unit: str | None = None
    fact_value_snapshot: dict[str, Any] | None = None
    ledger_event_id: UUID | None = None
    gaps: tuple[_PlannedGap, ...] = ()


def _hash(payload: dict[str, Any]) -> str:
    return payload_sha256(payload)[1]


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _minimum_evidence_similarity(requirement: DisclosureRequirement) -> Decimal:
    configured = requirement.evidence_rules.get(EVIDENCE_SIMILARITY_RULE)
    if configured is None:
        return DEFAULT_MIN_EVIDENCE_SIMILARITY
    if isinstance(configured, bool):
        raise AssuranceValidationError(
            "The evidence similarity threshold must be a decimal between 0 and 1.",
            field_details={EVIDENCE_SIMILARITY_RULE: "invalid"},
        )
    try:
        value = Decimal(str(configured))
    except Exception as error:
        raise AssuranceValidationError(
            "The evidence similarity threshold must be a decimal between 0 and 1.",
            field_details={EVIDENCE_SIMILARITY_RULE: "invalid"},
        ) from error
    if not value.is_finite() or value < Decimal(0) or value > Decimal(1):
        raise AssuranceValidationError(
            "The evidence similarity threshold must be a decimal between 0 and 1.",
            field_details={EVIDENCE_SIMILARITY_RULE: "out_of_range"},
        )
    return value.quantize(Decimal("0.000001"))


def _bounded_default_title(standard_name: str, period_name: str) -> str:
    separator = " - "
    suffix = period_name.strip()[:100]
    available = MAX_DRAFT_TITLE_LENGTH - len(separator) - len(suffix)
    prefix = standard_name.strip()[: max(1, available)].rstrip()
    title = f"{prefix}{separator}{suffix}" if suffix else prefix
    return title[:MAX_DRAFT_TITLE_LENGTH].rstrip()


def _standard_summary(standard: Any) -> AssuranceStandardSummary:
    return AssuranceStandardSummary(
        id=standard.id,
        company_id=standard.company_id,
        source_document_id=standard.source_document_id,
        code=standard.code,
        version=standard.version,
        name=standard.name,
        jurisdiction=standard.jurisdiction,
        description=standard.description,
        effective_from=standard.effective_from,
        effective_to=standard.effective_to,
        is_active=standard.is_active,
        created_at=standard.created_at,
        updated_at=standard.updated_at,
    )


def _requirement_view(requirement: DisclosureRequirement) -> AssuranceRequirementView:
    return AssuranceRequirementView(
        id=requirement.id,
        standard_id=requirement.standard_id,
        metric_definition_id=requirement.metric_definition_id,
        requirement_code=requirement.requirement_code,
        title=requirement.title,
        description=requirement.description,
        sequence=requirement.sequence,
        claim_template=requirement.claim_template,
        evidence_rules=requirement.evidence_rules,
        minimum_confidence=requirement.minimum_confidence,
        is_required=requirement.is_required,
        is_active=requirement.is_active,
    )


def _standard_detail(
    standard: Any,
    requirements: list[DisclosureRequirement] | tuple[DisclosureRequirement, ...],
) -> AssuranceStandardDetail:
    return AssuranceStandardDetail(
        **_standard_summary(standard).model_dump(),
        template=standard.template,
        requirements=[_requirement_view(item) for item in requirements],
    )


def _safe_evidence(record: EvidenceRecord) -> SafeEvidenceSummary:
    return SafeEvidenceSummary(
        id=record.evidence.id,
        source_document_id=record.document.id,
        data_source_id=record.source.id,
        source_filename=record.document.filename,
        source_document_checksum=record.document.checksum,
        evidence_type=record.evidence.evidence_type,
        locator=record.evidence.locator,
        checksum=record.evidence.checksum,
        metadata=record.evidence.evidence_metadata,
        embedding_model=record.evidence.embedding_model,
        embedded_at=record.evidence.embedded_at,
        similarity=record.similarity,
    )


def _approval_summary(approval: Approval | None) -> ApprovalPreviewSummary | None:
    if approval is None or approval.target_id is None:
        return None
    return ApprovalPreviewSummary(
        id=approval.id,
        target_type="disclosure_draft",
        target_id=approval.target_id,
        status=approval.status,
        preview_hash=approval.preview_hash,
        analysis_signature=approval.analysis_signature,
        context_hash=approval.context_hash,
        idempotency_key=approval.idempotency_key,
        expires_at=approval.expires_at,
        created_at=approval.created_at,
    )


def _cosine(left: list[float], right: list[float]) -> Decimal:
    if len(left) != len(right) or not left:
        return Decimal(0)
    numerator = math.fsum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(math.fsum(value * value for value in left))
    right_norm = math.sqrt(math.fsum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return Decimal(0)
    value = max(-1.0, min(1.0, numerator / (left_norm * right_norm)))
    return Decimal(str(round(value, 12)))


def _requirement_source(requirement: DisclosureRequirement) -> dict[str, Any]:
    return {
        "id": requirement.id,
        "requirement_code": requirement.requirement_code,
        "title": requirement.title,
        "description": requirement.description,
        "metric_definition_id": requirement.metric_definition_id,
        "sequence": requirement.sequence,
        "claim_template": requirement.claim_template,
        "evidence_rules": requirement.evidence_rules,
        "minimum_confidence": requirement.minimum_confidence,
        "is_required": requirement.is_required,
        "is_active": requirement.is_active,
    }


def _base_context(
    dependencies: DraftDependencies,
    requirements: list[DisclosureRequirement] | tuple[DisclosureRequirement, ...],
) -> dict[str, Any]:
    return {
        "company": {
            "id": dependencies.company.id,
            "code": dependencies.company.code,
            "name": dependencies.company.name,
            "is_synthetic": dependencies.company.is_synthetic,
            "is_active": dependencies.company.is_active,
        },
        "site": {
            "id": dependencies.site.id,
            "name": dependencies.site.name,
            "code": dependencies.site.code,
            "country_code": dependencies.site.country_code,
            "timezone": dependencies.site.timezone,
            "is_active": dependencies.site.is_active,
        },
        "reporting_period": {
            "id": dependencies.reporting_period.id,
            "name": dependencies.reporting_period.name,
            "start_date": dependencies.reporting_period.start_date,
            "end_date": dependencies.reporting_period.end_date,
            "status": dependencies.reporting_period.status,
        },
        "standard": {
            "id": dependencies.standard.id,
            "code": dependencies.standard.code,
            "version": dependencies.standard.version,
            "name": dependencies.standard.name,
            "jurisdiction": dependencies.standard.jurisdiction,
            "description": dependencies.standard.description,
            "template": dependencies.standard.template,
            "effective_from": dependencies.standard.effective_from,
            "effective_to": dependencies.standard.effective_to,
            "is_active": dependencies.standard.is_active,
            "source_document_id": dependencies.standard.source_document_id,
        },
        "requirements": [_requirement_source(item) for item in requirements],
        "measurement": {
            "id": dependencies.measurement.id,
            "metric_definition_id": dependencies.measurement.metric_definition_id,
            "site_id": dependencies.measurement.site_id,
            "reporting_period_id": dependencies.measurement.reporting_period_id,
            "ledger_event_id": dependencies.measurement.ledger_event_id,
            "value_kgco2e": dependencies.measurement.value_kgco2e,
            "unit": dependencies.measurement.unit,
            "confidence": dependencies.measurement.confidence,
            "status": dependencies.measurement.status,
            "output_hash": dependencies.measurement.output_hash,
            "formula": dependencies.measurement.formula,
        },
        "agent_run_id": dependencies.agent_run.id if dependencies.agent_run else None,
        "validation_method": VALIDATION_METHOD_ID,
        "retrieval_method": RETRIEVAL_METHOD_ID,
        "embedding_model": EMBEDDING_MODEL_ID,
    }


def _source_state(
    dependencies: DraftDependencies,
    requirements: list[DisclosureRequirement] | tuple[DisclosureRequirement, ...],
    evidence: list[EvidenceRecord] | tuple[EvidenceRecord, ...],
    measurement_event: Any | None,
) -> dict[str, Any]:
    state = _base_context(dependencies, requirements)
    state["measurement_ledger_payload_hash"] = (
        measurement_event.payload_hash if measurement_event is not None else None
    )
    state["evidence"] = [
        {
            "id": item.evidence.id,
            "evidence_type": item.evidence.evidence_type,
            "locator": item.evidence.locator,
            "checksum": item.evidence.checksum,
            "metadata": item.evidence.evidence_metadata,
            "embedding_model": item.evidence.embedding_model,
            "embedding_hash": _hash(
                {
                    "embedding": (
                        list(item.evidence.embedding) if item.evidence.embedding is not None else []
                    )
                }
            ),
            "similarity": (_decimal(item.similarity) if item.similarity is not None else None),
            "source_document_id": item.document.id,
            "source_document_checksum": item.document.checksum,
            "source_document": {
                "filename": item.document.filename,
                "content_type": item.document.content_type,
                "version": item.document.version,
                "size_bytes": item.document.size_bytes,
                "storage_uri": item.document.storage_uri,
                "metadata": item.document.document_metadata,
            },
            "source": {
                "id": item.source.id,
                "site_id": item.source.site_id,
                "name": item.source.name,
                "source_type": item.source.source_type,
                "status": item.source.status,
                "external_reference": item.source.external_reference,
                "configuration": item.source.configuration,
                "is_synthetic": item.source.is_synthetic,
            },
        }
        for item in sorted(
            evidence,
            key=lambda item: (item.document.checksum, item.evidence.locator, str(item.evidence.id)),
        )
    ]
    return state


def _claim_type(requirement: DisclosureRequirement) -> str:
    if requirement.evidence_rules.get("fact_binding_required") is True:
        return "numeric"
    placeholders = set(_PLACEHOLDER.findall(requirement.claim_template))
    if placeholders & {"company", "site", "reporting_period"}:
        return "scope"
    return "qualitative"


def _gap(
    requirement: DisclosureRequirement,
    suffix: str,
    message: str,
    **details: Any,
) -> _PlannedGap:
    code = f"{requirement.requirement_code}_{suffix}"[:100]
    return _PlannedGap(
        code=code,
        severity="error" if requirement.is_required else "warning",
        message=message,
        details={"requirement_code": requirement.requirement_code, **details},
    )


class AssuranceServicePort(Protocol):
    """Typed application port exposed to HTTP and bounded agent runtimes."""

    async def create_draft(
        self,
        request: DisclosureDraftCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> DisclosureDraftView: ...

    async def get_draft(
        self,
        *,
        company_id: UUID,
        draft_id: UUID,
    ) -> DisclosureDraftView: ...

    async def validate_for_agent(
        self,
        draft_id: UUID,
        request: DisclosureDraftValidateRequest,
        *,
        trace_id: str | None = None,
    ) -> AssuranceAgentResult: ...

    async def get_evidence_pack(
        self,
        *,
        company_id: UUID,
        draft_id: UUID,
    ) -> AssuranceEvidencePack: ...


class AssuranceService:
    """Application boundary used by HTTP routes and bounded agent tools."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = AssuranceRepository(session)

    async def list_standards(
        self,
        *,
        company_id: UUID,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> AssuranceStandardListResponse:
        standards, total = await self.repository.list_standards(
            company_id=company_id,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        items = []
        for standard in standards:
            requirements = await self.repository.list_requirements(
                company_id=company_id,
                standard_id=standard.id,
                active_only=active_only,
            )
            items.append(_standard_detail(standard, requirements))
        return AssuranceStandardListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )

    async def create_draft(
        self,
        request: DisclosureDraftCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> DisclosureDraftView:
        request_hash = _hash(request.model_dump(mode="python"))
        existing = await self.repository.find_draft_by_idempotency_key(
            company_id=request.company_id,
            idempotency_key=request.idempotency_key,
        )
        if existing is not None:
            if existing.validation_summary.get("create_request_hash") != request_hash:
                raise AssuranceConflictError(
                    "The idempotency key is already bound to a different draft request.",
                    field_details={"idempotency_key": "conflict"},
                )
            return await self.get_draft(company_id=request.company_id, draft_id=existing.id)

        dependencies = await self.repository.load_draft_dependencies(
            company_id=request.company_id,
            standard_id=request.standard_id,
            site_id=request.site_id,
            reporting_period_id=request.reporting_period_id,
            measurement_id=request.measurement_id,
            requested_by=request.requested_by,
            agent_run_id=request.agent_run_id,
        )
        if dependencies is None:
            raise AssuranceNotFoundError(
                "The requested Assurance context was not found in this company."
            )
        requirements = await self.repository.list_requirements(
            company_id=request.company_id,
            standard_id=request.standard_id,
        )
        self._validate_draft_dependencies(dependencies, requirements)
        version = await self.repository.allocate_draft_version(
            company_id=request.company_id,
            standard_id=request.standard_id,
            reporting_period_id=request.reporting_period_id,
        )
        if version is None:
            raise AssuranceNotFoundError("The selected disclosure standard was not found.")

        # The standard row lock acquired by version allocation serializes draft
        # creation. Recheck the JSON-backed idempotency key while holding it so
        # concurrent identical requests cannot create successive versions.
        locked_existing = await self.repository.find_draft_by_idempotency_key(
            company_id=request.company_id,
            idempotency_key=request.idempotency_key,
        )
        if locked_existing is not None:
            locked_existing_id = locked_existing.id
            locked_request_hash = locked_existing.validation_summary.get("create_request_hash")
            await self.session.rollback()
            if locked_request_hash != request_hash:
                raise AssuranceConflictError(
                    "The idempotency key is already bound to a different draft request.",
                    field_details={"idempotency_key": "conflict"},
                )
            return await self.get_draft(
                company_id=request.company_id,
                draft_id=locked_existing_id,
            )

        context = _base_context(dependencies, requirements)
        context_hash = _hash(context)
        narrative_template = "\n\n".join(
            [item.claim_template for item in requirements] + [DISCLAIMER]
        )
        title = request.title or _bounded_default_title(
            dependencies.standard.name,
            dependencies.reporting_period.name,
        )
        initial_payload = {
            "target_type": "disclosure_draft",
            "state": "draft",
            "company_id": request.company_id,
            "standard_id": request.standard_id,
            "site_id": request.site_id,
            "reporting_period_id": request.reporting_period_id,
            "measurement_id": request.measurement_id,
            "version": version,
            "title": title,
            "context_hash": context_hash,
        }
        validation_summary = {
            "state": "not_validated",
            "terminal_state": "no_data",
            "idempotency_key": request.idempotency_key,
            "create_request_hash": request_hash,
            "measurement_id": str(request.measurement_id),
            "requested_by": str(request.requested_by),
            "base_context_hash": context_hash,
            "validation_method": VALIDATION_METHOD_ID,
            "retrieval_method": RETRIEVAL_METHOD_ID,
            "embedding_model": EMBEDDING_MODEL_ID,
            "disclaimer": DISCLAIMER,
        }
        try:
            draft = await self.repository.create_draft(
                company_id=request.company_id,
                standard_id=request.standard_id,
                site_id=request.site_id,
                reporting_period_id=request.reporting_period_id,
                agent_run_id=request.agent_run_id,
                version=version,
                title=title,
                narrative_template=narrative_template,
                context_hash=context_hash,
                payload_hash=_hash(initial_payload),
                validation_summary=validation_summary,
            )
            initial_payload["target_id"] = draft.id
            draft.payload_hash = _hash(initial_payload)
            ledger_event = await append_ledger_event(
                self.session,
                company_id=request.company_id,
                event_type="disclosure_draft_created",
                entity_type="disclosure_draft",
                entity_id=draft.id,
                payload=initial_payload,
                analysis_signature=context_hash,
                created_by=request.requested_by,
            )
            draft.ledger_event_id = ledger_event.id
            self.session.add(
                AuditLog(
                    company_id=request.company_id,
                    actor_id=request.requested_by,
                    agent_run_id=request.agent_run_id,
                    action="assurance.draft_created",
                    entity_type="disclosure_draft",
                    entity_id=draft.id,
                    trace_id=trace_id,
                    details={
                        "context_hash": context_hash,
                        "payload_hash": draft.payload_hash,
                        "standard_id": str(request.standard_id),
                        "measurement_id": str(request.measurement_id),
                    },
                )
            )
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            replay = await self.repository.find_draft_by_idempotency_key(
                company_id=request.company_id,
                idempotency_key=request.idempotency_key,
            )
            if (
                replay is not None
                and replay.validation_summary.get("create_request_hash") == request_hash
            ):
                return await self.get_draft(company_id=request.company_id, draft_id=replay.id)
            raise AssuranceConflictError(
                "The draft conflicts with an existing disclosure version."
            ) from error
        except Exception:
            await self.session.rollback()
            raise
        return await self.get_draft(company_id=request.company_id, draft_id=draft.id)

    async def get_draft(self, *, company_id: UUID, draft_id: UUID) -> DisclosureDraftView:
        aggregate = await self.repository.load_draft_aggregate(
            company_id=company_id,
            draft_id=draft_id,
        )
        if aggregate is None:
            raise AssuranceNotFoundError("The requested disclosure draft was not found.")
        return self._draft_view(aggregate)

    async def validate_draft(
        self,
        draft_id: UUID,
        request: DisclosureDraftValidateRequest,
        *,
        trace_id: str | None = None,
    ) -> DisclosureDraftValidationResult:
        draft = await self.repository.get_draft(
            company_id=request.company_id,
            draft_id=draft_id,
            for_update=True,
        )
        if draft is None:
            raise AssuranceNotFoundError("The requested disclosure draft was not found.")
        actor = await self.repository.get_actor(
            company_id=request.company_id,
            actor_id=request.requested_by,
        )
        if actor is None or not actor.is_active:
            raise AssuranceValidationError(
                "An active actor in the draft company must request validation.",
                field_details={"requested_by": "inactive_or_not_found"},
            )
        if (
            request.expected_context_hash is not None
            and request.expected_context_hash != draft.context_hash
        ):
            raise AssuranceConflictError(
                "The draft context changed after it was read.",
                field_details={"expected_context_hash": "mismatch"},
            )

        existing_claims = await self.repository.list_claims(
            company_id=request.company_id,
            draft_id=draft_id,
        )
        if existing_claims:
            aggregate = await self.repository.load_draft_aggregate(
                company_id=request.company_id,
                draft_id=draft_id,
            )
            if aggregate is None:
                raise AssuranceNotFoundError("The disclosure draft became unavailable.")
            if await self._aggregate_is_stale(aggregate):
                return await self._invalidate_draft(
                    aggregate,
                    actor_id=request.requested_by,
                    trace_id=trace_id,
                )
            return self._validation_result(aggregate, idempotent=True)

        measurement_id = self._summary_uuid(draft.validation_summary, "measurement_id")
        if measurement_id is None:
            raise AssuranceValidationError("The draft is missing its bound measurement identity.")
        dependencies = await self.repository.load_draft_dependencies(
            company_id=request.company_id,
            standard_id=draft.standard_id,
            site_id=draft.site_id,
            reporting_period_id=draft.reporting_period_id,
            measurement_id=measurement_id,
            requested_by=request.requested_by,
            agent_run_id=draft.agent_run_id,
        )
        if dependencies is None:
            return await self._invalidate_missing_dependencies(
                draft,
                actor_id=request.requested_by,
                trace_id=trace_id,
            )
        requirements = await self.repository.list_requirements(
            company_id=request.company_id,
            standard_id=draft.standard_id,
        )
        self._validate_draft_dependencies(dependencies, requirements)
        measurement_event = (
            await self.repository.get_ledger_event(
                company_id=request.company_id,
                event_id=dependencies.measurement.ledger_event_id,
            )
            if dependencies.measurement.ledger_event_id is not None
            else None
        )

        draft.status = "validating"
        plans = [
            await self._plan_claim(draft, dependencies, requirement) for requirement in requirements
        ]
        selected_evidence = self._unique_evidence(
            [item for plan in plans for item in plan.evidence]
        )
        source_state = _source_state(
            dependencies,
            requirements,
            selected_evidence,
            measurement_event,
        )
        source_hash = _hash(source_state)
        final_context_hash = _hash(
            {
                "base_context": _base_context(dependencies, requirements),
                "source_hash": source_hash,
            }
        )
        analysis_signature = _hash(
            {
                "context_hash": final_context_hash,
                "validation_method": VALIDATION_METHOD_ID,
                "retrieval_method": RETRIEVAL_METHOD_ID,
            }
        )
        draft.context_hash = final_context_hash

        try:
            planned_bindings: dict[str, tuple[dict[str, Any], FactBinding]] = {}
            for plan in plans:
                await self._persist_claim(
                    draft=draft,
                    dependencies=dependencies,
                    plan=plan,
                    context_hash=final_context_hash,
                    planned_bindings=planned_bindings,
                )
            await self.repository.flush()
            aggregate = await self.repository.load_draft_aggregate(
                company_id=request.company_id,
                draft_id=draft_id,
            )
            if aggregate is None:
                raise AssuranceNotFoundError("The disclosure draft became unavailable.")

            blocked = any(
                claim.support_status != "supported"
                and self._requirement_is_required(claim.requirement_id, requirements)
                for claim in aggregate.claims
            ) or any(gap.status == "open" and gap.severity == "error" for gap in aggregate.gaps)
            now = datetime.now(UTC)
            expires_at = None if blocked else now + APPROVAL_TTL
            draft.rendered_text = self._render_draft(aggregate)
            preview_payload = self._preview_payload(
                aggregate,
                context_hash=final_context_hash,
                analysis_signature=analysis_signature,
                approval_expires_at=expires_at,
                approval_eligible=not blocked,
            )
            payload_hash = _hash(preview_payload)
            draft.payload_hash = payload_hash
            draft.status = "blocked" if blocked else "pending_approval"
            terminal_state = "unsupported" if blocked else "approval_required"
            summary = dict(draft.validation_summary)
            summary.update(
                {
                    "state": "validated",
                    "terminal_state": terminal_state,
                    "validation_idempotency_key": request.idempotency_key,
                    "validation_method": VALIDATION_METHOD_ID,
                    "retrieval_method": RETRIEVAL_METHOD_ID,
                    "embedding_model": EMBEDDING_MODEL_ID,
                    "source_hash": source_hash,
                    "analysis_signature": analysis_signature,
                    "validated_at": now.isoformat(),
                    "supported_claim_codes": sorted(self._claim_codes(aggregate, supported=True)),
                    "unsupported_claim_codes": sorted(
                        self._claim_codes(aggregate, supported=False)
                    ),
                    "disclaimer": DISCLAIMER,
                }
            )
            draft.validation_summary = summary
            event = await append_ledger_event(
                self.session,
                company_id=draft.company_id,
                event_type=(
                    "disclosure_draft_blocked" if blocked else "disclosure_draft_validated"
                ),
                entity_type="disclosure_draft",
                entity_id=draft.id,
                payload=preview_payload,
                analysis_signature=analysis_signature,
                created_by=request.requested_by,
            )
            previous_event_id = draft.ledger_event_id
            draft.ledger_event_id = event.id
            if previous_event_id is not None:
                await append_lineage_edge(
                    self.session,
                    company_id=draft.company_id,
                    parent_event_id=previous_event_id,
                    child_event_id=event.id,
                    relationship_type="validated_as",
                    metadata={"terminal_state": terminal_state},
                )
            if dependencies.measurement.ledger_event_id is not None:
                await append_lineage_edge(
                    self.session,
                    company_id=draft.company_id,
                    parent_event_id=dependencies.measurement.ledger_event_id,
                    child_event_id=event.id,
                    relationship_type="supports_disclosure",
                    metadata={"measurement_id": str(dependencies.measurement.id)},
                )
            for evidence in selected_evidence:
                await attach_ledger_evidence(
                    self.session,
                    company_id=draft.company_id,
                    ledger_event_id=event.id,
                    evidence_item_id=evidence.evidence.id,
                    relevance="Validated disclosure claim support",
                )

            if not blocked and expires_at is not None:
                approval_request = GenericApprovalPreviewRequest(
                    company_id=draft.company_id,
                    target_type="disclosure_draft",
                    target_id=draft.id,
                    requested_by=request.requested_by,
                    preview_payload=preview_payload,
                    preview_hash=payload_hash,
                    analysis_signature=analysis_signature,
                    context_hash=final_context_hash,
                    expires_at=expires_at,
                )
                try:
                    await create_generic_approval_preview(self.session, approval_request)
                except ApprovalServiceError as error:
                    raise AssuranceConflictError(
                        "The disclosure approval preview could not be created.",
                        field_details=error.field_details,
                    ) from error

            self.session.add(
                AuditLog(
                    company_id=draft.company_id,
                    actor_id=request.requested_by,
                    agent_run_id=draft.agent_run_id,
                    action=(
                        "assurance.validation_blocked"
                        if blocked
                        else "assurance.validation_completed"
                    ),
                    entity_type="disclosure_draft",
                    entity_id=draft.id,
                    trace_id=trace_id,
                    details={
                        "context_hash": final_context_hash,
                        "payload_hash": payload_hash,
                        "analysis_signature": analysis_signature,
                        "terminal_state": terminal_state,
                    },
                )
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        aggregate = await self.repository.load_draft_aggregate(
            company_id=request.company_id,
            draft_id=draft_id,
        )
        if aggregate is None:
            raise AssuranceNotFoundError("The validated disclosure draft was not found.")
        return self._validation_result(aggregate, idempotent=False)

    async def get_evidence_pack(
        self,
        *,
        company_id: UUID,
        draft_id: UUID,
    ) -> AssuranceEvidencePack:
        aggregate = await self.repository.load_draft_aggregate(
            company_id=company_id,
            draft_id=draft_id,
        )
        if aggregate is None:
            raise AssuranceNotFoundError("The requested disclosure draft was not found.")
        if (
            aggregate.draft.status == "invalidated"
            or aggregate.draft.validation_summary.get("terminal_state") == "stale"
        ):
            raise AssuranceStaleError(
                "The disclosure draft is stale and cannot produce an evidence pack."
            )
        if not aggregate.claims or aggregate.draft.validation_summary.get("state") != "validated":
            raise AssuranceConflictError(
                "The disclosure draft must be validated before exporting evidence.",
                code="assurance_validation_required",
                field_details={"draft_id": "validation_required"},
            )
        if await self._aggregate_is_stale(aggregate):
            raise AssuranceStaleError("The disclosure draft inputs changed after validation.")
        draft_view = self._draft_view(aggregate)
        evidence = sorted(
            (_safe_evidence(item) for item in aggregate.evidence),
            key=lambda item: (item.source_document_checksum, item.locator, str(item.id)),
        )
        fact_bindings = sorted(
            (self._fact_binding_view(item) for item in aggregate.fact_bindings),
            key=lambda item: (item.placeholder, str(item.id)),
        )
        return AssuranceEvidencePack(
            generated_at=datetime.now(UTC),
            company_id=company_id,
            draft_id=draft_id,
            standard_code=aggregate.standard.code,
            standard_version=aggregate.standard.version,
            context_hash=aggregate.draft.context_hash,
            payload_hash=aggregate.draft.payload_hash,
            claims=draft_view.claims,
            gaps=draft_view.gaps,
            evidence=evidence,
            fact_bindings=fact_bindings,
        )

    async def validate_for_agent(
        self,
        draft_id: UUID,
        request: DisclosureDraftValidateRequest,
        *,
        trace_id: str | None = None,
    ) -> AssuranceAgentResult:
        result = await self.validate_draft(draft_id, request, trace_id=trace_id)
        supported = sorted(
            item.requirement_code
            for item in result.draft.claims
            if item.support_status == "supported" and item.requirement_code is not None
        )
        unsupported = sorted(
            item.requirement_code
            for item in result.draft.claims
            if item.support_status != "supported" and item.requirement_code is not None
        )
        return AssuranceAgentResult(
            draft_id=draft_id,
            terminal_state=result.terminal_state,
            status=result.draft.status,
            supported_claim_codes=supported,
            unsupported_claim_codes=unsupported,
            evidence_gap_codes=sorted(
                item.code for item in result.draft.gaps if item.status == "open"
            ),
            approval_id=(result.draft.approval.id if result.draft.approval else None),
            context_hash=result.draft.context_hash,
            payload_hash=result.draft.payload_hash,
            idempotent=result.idempotent,
        )

    def _validate_draft_dependencies(
        self,
        dependencies: DraftDependencies,
        requirements: list[DisclosureRequirement] | tuple[DisclosureRequirement, ...],
    ) -> None:
        standard = dependencies.standard
        period = dependencies.reporting_period
        measurement = dependencies.measurement
        if not dependencies.company.is_active:
            raise AssuranceValidationError(
                "The draft company is inactive.",
                field_details={"company_id": "inactive"},
            )
        if not dependencies.site.is_active:
            raise AssuranceValidationError(
                "The draft site is inactive.",
                field_details={"site_id": "inactive"},
            )
        if (
            not standard.is_active
            or standard.effective_from > period.end_date
            or (standard.effective_to is not None and standard.effective_to < period.start_date)
        ):
            raise AssuranceValidationError(
                "The selected standard is not effective for the reporting period."
            )
        if not requirements:
            raise AssuranceValidationError("The selected standard has no active requirements.")
        sequences = [item.sequence for item in requirements]
        if any(sequence <= 0 for sequence in sequences) or len(set(sequences)) != len(sequences):
            raise AssuranceValidationError(
                "Active disclosure requirement sequences must be unique positive integers.",
                field_details={"requirements": "invalid_sequence"},
            )
        if not dependencies.requested_by.is_active:
            raise AssuranceValidationError("The requesting actor is inactive.")
        if dependencies.agent_run is not None and (
            dependencies.agent_run.actor_id != dependencies.requested_by.id
        ):
            raise AssuranceValidationError(
                "The agent run is not owned by the requesting actor.",
                field_details={"agent_run_id": "actor_mismatch"},
            )
        if measurement.site_id != dependencies.site.id or (
            measurement.reporting_period_id != period.id
        ):
            raise AssuranceValidationError(
                "The measurement context does not match the draft context."
            )
        if measurement.status != "verified" or measurement.ledger_event_id is None:
            raise AssuranceValidationError(
                "Only a verified measurement with ledger lineage can support a draft."
            )
        fact_requirements = [
            item
            for item in requirements
            if item.evidence_rules.get("fact_binding_required") is True
        ]
        if fact_requirements and dependencies.agent_run is None:
            raise AssuranceValidationError(
                "A persisted agent run is required to bind numerical disclosure facts.",
                field_details={"agent_run_id": "required_for_fact_binding"},
            )
        mismatched_metrics = [
            item.requirement_code
            for item in fact_requirements
            if item.metric_definition_id is not None
            and item.metric_definition_id != measurement.metric_definition_id
        ]
        if mismatched_metrics:
            raise AssuranceValidationError(
                "The measurement metric does not satisfy the disclosure requirements.",
                field_details={"requirement_codes": ",".join(mismatched_metrics)},
            )

    async def _plan_claim(
        self,
        draft: Any,
        dependencies: DraftDependencies,
        requirement: DisclosureRequirement,
    ) -> _PlannedClaim:
        evidence = await self._retrieve_evidence(dependencies, requirement)
        claim_type = _claim_type(requirement)
        placeholders = set(_PLACEHOLDER.findall(requirement.claim_template))
        replacements = {
            "company": dependencies.company.name,
            "site": dependencies.site.name,
            "reporting_period": dependencies.reporting_period.name,
        }
        details: dict[str, Any] = {
            "requirement_code": requirement.requirement_code,
            "validation_method": VALIDATION_METHOD_ID,
            "retrieval_method": RETRIEVAL_METHOD_ID,
            "minimum_confidence": _decimal(requirement.minimum_confidence),
            "minimum_evidence_similarity": _decimal(_minimum_evidence_similarity(requirement)),
            "candidate_evidence_ids": [str(item.evidence.id) for item in evidence],
        }

        if requirement.evidence_rules.get("comparable_prior_period_required") is True:
            gap = _gap(
                requirement,
                "COMPARABLE_PRIOR_PERIOD_MISSING",
                "A comparable verified prior-period fact is required but unavailable.",
                terminal_state="unsupported",
            )
            return _PlannedClaim(
                requirement=requirement,
                claim_type="numeric",
                rendered_text=None,
                support_status="unsupported",
                confidence=Decimal(0),
                validation_details={**details, "reason": "comparable_prior_period_missing"},
                evidence=(),
                gaps=(gap,),
            )

        if requirement.evidence_rules.get("fact_binding_required") is True:
            return self._plan_numeric_claim(
                dependencies,
                requirement,
                evidence,
                placeholders,
                details,
            )

        unknown = placeholders - set(replacements)
        if unknown:
            gap = _gap(
                requirement,
                "UNKNOWN_PLACEHOLDER",
                "The claim template contains an unbound placeholder.",
                placeholders=sorted(unknown),
            )
            return _PlannedClaim(
                requirement=requirement,
                claim_type=claim_type,
                rendered_text=None,
                support_status="unsupported",
                confidence=Decimal(0),
                validation_details={**details, "unknown_placeholders": sorted(unknown)},
                evidence=(),
                gaps=(gap,),
            )
        if not evidence:
            gap = _gap(
                requirement,
                "EVIDENCE_MISSING",
                "No tenant- and context-valid evidence supports this claim.",
            )
            return _PlannedClaim(
                requirement=requirement,
                claim_type=claim_type,
                rendered_text=None,
                support_status="unsupported",
                confidence=Decimal(0),
                validation_details={**details, "reason": "evidence_missing"},
                evidence=(),
                gaps=(gap,),
            )
        rendered = requirement.claim_template
        for placeholder, value in replacements.items():
            rendered = rendered.replace(f"{{{placeholder}}}", value)
        return _PlannedClaim(
            requirement=requirement,
            claim_type=claim_type,
            rendered_text=rendered,
            support_status="supported",
            confidence=Decimal(1),
            validation_details={**details, "citation_validation": "valid"},
            evidence=evidence,
        )

    def _plan_numeric_claim(
        self,
        dependencies: DraftDependencies,
        requirement: DisclosureRequirement,
        evidence: tuple[EvidenceRecord, ...],
        placeholders: set[str],
        details: dict[str, Any],
    ) -> _PlannedClaim:
        measurement = dependencies.measurement
        allowed = {"scope2_total"}
        unknown = placeholders - allowed
        expected_unit = requirement.evidence_rules.get("unit")
        reasons: list[str] = []
        if unknown or "scope2_total" not in placeholders:
            reasons.append("unbound_numeric_placeholder")
        if (
            expected_unit is not None
            and str(expected_unit).casefold() != measurement.unit.casefold()
        ):
            reasons.append("incompatible_unit")
        if measurement.confidence < requirement.minimum_confidence:
            reasons.append("confidence_below_threshold")
        if measurement.status != "verified" or measurement.ledger_event_id is None:
            reasons.append("measurement_not_verified")
        if not evidence:
            reasons.append("evidence_missing")
        if reasons:
            gap = _gap(
                requirement,
                "NUMERIC_SUPPORT_INVALID",
                "The numerical claim does not have complete verified support.",
                reasons=reasons,
            )
            return _PlannedClaim(
                requirement=requirement,
                claim_type="numeric",
                rendered_text=None,
                support_status="unsupported",
                confidence=Decimal(0),
                validation_details={**details, "reasons": reasons},
                evidence=(),
                gaps=(gap,),
            )
        display_value = f"{_decimal(measurement.value_kgco2e)} {measurement.unit}"
        rendered = requirement.claim_template.replace("{scope2_total}", display_value)
        value_snapshot = {
            "measurement_id": measurement.id,
            "metric_definition_id": measurement.metric_definition_id,
            "site_id": measurement.site_id,
            "reporting_period_id": measurement.reporting_period_id,
            "ledger_event_id": measurement.ledger_event_id,
            "value": measurement.value_kgco2e,
            "unit": measurement.unit,
            "confidence": measurement.confidence,
            "status": measurement.status,
            "output_hash": measurement.output_hash,
        }
        return _PlannedClaim(
            requirement=requirement,
            claim_type="numeric",
            rendered_text=rendered,
            support_status="supported",
            confidence=measurement.confidence,
            validation_details={**details, "fact_validation": "valid"},
            evidence=evidence,
            fact_placeholder="fact_scope2_total",
            fact_display_value=display_value,
            fact_unit=measurement.unit,
            fact_value_snapshot=value_snapshot,
            ledger_event_id=measurement.ledger_event_id,
        )

    async def _retrieve_evidence(
        self,
        dependencies: DraftDependencies,
        requirement: DisclosureRequirement,
    ) -> tuple[EvidenceRecord, ...]:
        configured = requirement.evidence_rules.get("allowed_evidence_types")
        evidence_types = (
            tuple(str(item) for item in configured)
            if isinstance(configured, list) and configured
            else ("disclosure_support",)
        )
        query_text = (
            f"{requirement.requirement_code}\n{requirement.title}\n"
            f"{requirement.description}\n{requirement.claim_template}"
        )
        query_embedding = hash_embedding(query_text)
        records = await self.repository.list_evidence_candidates(
            company_id=dependencies.standard.company_id,
            evidence_types=evidence_types,
            embedding_model=EMBEDDING_MODEL_ID,
            limit=MAX_EVIDENCE_CANDIDATES,
            query_embedding=query_embedding,
        )
        candidates: list[EvidenceCandidate] = []
        by_id: dict[UUID, EvidenceRecord] = {}
        for record in records:
            embedding = record.evidence.embedding
            similarity = (
                _cosine(query_embedding, list(embedding)) if embedding is not None else Decimal(0)
            )
            candidate = EvidenceCandidate(
                evidence_id=record.evidence.id,
                company_id=record.evidence.company_id,
                source_document_id=record.document.id,
                data_source_id=record.source.id,
                source_site_id=record.source.site_id,
                evidence_type=record.evidence.evidence_type,
                locator=record.evidence.locator,
                content_text=record.evidence.content_text,
                checksum=record.evidence.checksum,
                source_document_checksum=record.document.checksum,
                metadata=record.evidence.evidence_metadata,
                embedding_model=record.evidence.embedding_model,
                embedded_at=record.evidence.embedded_at,
                similarity=similarity,
            )
            candidates.append(candidate)
            by_id[candidate.evidence_id] = EvidenceRecord(
                record.evidence,
                record.document,
                record.source,
                similarity,
            )
        context = EvidenceRetrievalContext(
            company_id=dependencies.standard.company_id,
            site_id=dependencies.site.id,
            reporting_period_id=dependencies.reporting_period.id,
            requirement_code=requirement.requirement_code,
            allowed_evidence_types=evidence_types,
            embedding_model=EMBEDDING_MODEL_ID,
            minimum_similarity=_minimum_evidence_similarity(requirement),
            company_name=dependencies.company.name,
            site_name=dependencies.site.name,
            reporting_period_name=dependencies.reporting_period.name,
        )
        ranked = rank_evidence_candidates(
            candidates,
            context,
            limit=MAX_EVIDENCE_PER_CLAIM,
        )
        return tuple(by_id[item.evidence_id] for item in ranked)

    async def _persist_claim(
        self,
        *,
        draft: Any,
        dependencies: DraftDependencies,
        plan: _PlannedClaim,
        context_hash: str,
        planned_bindings: dict[str, tuple[dict[str, Any], FactBinding]],
    ) -> None:
        fact_binding_id = None
        if plan.fact_placeholder is not None:
            if (
                dependencies.agent_run is None
                or plan.fact_value_snapshot is None
                or plan.ledger_event_id is None
                or plan.fact_display_value is None
            ):
                raise AssuranceValidationError(
                    "A numerical claim cannot be persisted without an agent run and fact."
                )
            normalized_snapshot = normalize_json(plan.fact_value_snapshot)
            if not isinstance(normalized_snapshot, dict):
                raise AssuranceValidationError("The numerical fact snapshot is not a JSON object.")
            binding_payload = {
                "artifact_type": "disclosure_draft",
                "artifact_id": draft.id,
                "placeholder": plan.fact_placeholder,
                "ledger_event_id": plan.ledger_event_id,
                "evidence_item_id": (plan.evidence[0].evidence.id if plan.evidence else None),
                "value_snapshot": normalized_snapshot,
                "display_value": plan.fact_display_value,
                "unit": plan.fact_unit,
                "context_hash": context_hash,
            }
            normalized_payload = normalize_json(binding_payload)
            if not isinstance(normalized_payload, dict):  # pragma: no cover - fixed shape
                raise AssuranceValidationError("The fact binding payload is invalid.")
            existing_binding = planned_bindings.get(plan.fact_placeholder)
            if existing_binding is not None:
                existing_payload, binding = existing_binding
                if existing_payload != normalized_payload:
                    raise AssuranceValidationError(
                        "Two supported claims resolve the same placeholder to different facts.",
                        field_details={"placeholder": plan.fact_placeholder},
                    )
            else:
                binding = FactBinding(
                    company_id=draft.company_id,
                    artifact_type="disclosure_draft",
                    artifact_id=draft.id,
                    recommendation_id=None,
                    agent_run_id=dependencies.agent_run.id,
                    ledger_event_id=plan.ledger_event_id,
                    evidence_item_id=(plan.evidence[0].evidence.id if plan.evidence else None),
                    placeholder=plan.fact_placeholder,
                    value_snapshot=normalized_snapshot,
                    display_value=plan.fact_display_value,
                    unit=plan.fact_unit,
                    context_hash=context_hash,
                    binding_hash=_hash(normalized_payload),
                )
                self.session.add(binding)
                await self.session.flush()
                planned_bindings[plan.fact_placeholder] = (normalized_payload, binding)
            fact_binding_id = binding.id

        claim = await self.repository.create_claim(
            company_id=draft.company_id,
            disclosure_draft_id=draft.id,
            requirement_id=plan.requirement.id,
            fact_binding_id=fact_binding_id,
            ledger_event_id=plan.ledger_event_id,
            sequence=plan.requirement.sequence,
            claim_type=plan.claim_type,
            claim_template=plan.requirement.claim_template,
            rendered_text=plan.rendered_text,
            support_status=plan.support_status,
            confidence=plan.confidence,
            validation_details=plan.validation_details,
            validated_at=datetime.now(UTC),
        )
        for evidence in plan.evidence:
            existing = await self.repository.get_citation_by_sources(
                company_id=draft.company_id,
                claim_id=claim.id,
                ledger_event_id=plan.ledger_event_id,
                evidence_item_id=evidence.evidence.id,
            )
            if existing is not None:
                continue
            await self.repository.create_citation(
                company_id=draft.company_id,
                disclosure_claim_id=claim.id,
                ledger_event_id=plan.ledger_event_id,
                evidence_item_id=evidence.evidence.id,
                locator=evidence.evidence.locator,
                validation_status="valid",
                validation_details={
                    "requirement_code": plan.requirement.requirement_code,
                    "evidence_checksum": evidence.evidence.checksum,
                    "source_document_checksum": evidence.document.checksum,
                    "embedding_model": evidence.evidence.embedding_model,
                    "similarity": (
                        _decimal(evidence.similarity) if evidence.similarity is not None else None
                    ),
                    "context_hash": context_hash,
                },
            )
        for gap in plan.gaps:
            await self.repository.create_gap(
                company_id=draft.company_id,
                disclosure_draft_id=draft.id,
                disclosure_claim_id=claim.id,
                requirement_id=plan.requirement.id,
                code=gap.code,
                severity=gap.severity,
                status="open",
                message=gap.message,
                details=gap.details,
            )

    def _draft_view(self, aggregate: DraftAggregate) -> DisclosureDraftView:
        requirements = {item.id: item for item in aggregate.requirements}
        evidence = {item.evidence.id: item for item in aggregate.evidence}
        citations_by_claim: dict[UUID, list[ClaimCitationView]] = {}
        for citation in aggregate.citations:
            evidence_view = (
                _safe_evidence(evidence[citation.evidence_item_id])
                if citation.evidence_item_id in evidence
                else None
            )
            citations_by_claim.setdefault(citation.disclosure_claim_id, []).append(
                ClaimCitationView(
                    id=citation.id,
                    disclosure_claim_id=citation.disclosure_claim_id,
                    ledger_event_id=citation.ledger_event_id,
                    evidence_item_id=citation.evidence_item_id,
                    locator=citation.locator,
                    validation_status=citation.validation_status,
                    validation_details=citation.validation_details,
                    evidence=evidence_view,
                    created_at=citation.created_at,
                )
            )
        claims = []
        for claim in aggregate.claims:
            requirement = requirements.get(claim.requirement_id)
            claims.append(
                DisclosureClaimView(
                    id=claim.id,
                    disclosure_draft_id=claim.disclosure_draft_id,
                    requirement_id=claim.requirement_id,
                    requirement_code=(requirement.requirement_code if requirement else None),
                    fact_binding_id=claim.fact_binding_id,
                    ledger_event_id=claim.ledger_event_id,
                    sequence=claim.sequence,
                    claim_type=claim.claim_type,
                    claim_template=claim.claim_template,
                    rendered_text=claim.rendered_text,
                    support_status=claim.support_status,
                    confidence=claim.confidence,
                    validation_details=claim.validation_details,
                    validated_at=claim.validated_at,
                    created_at=claim.created_at,
                    updated_at=claim.updated_at,
                    citations=citations_by_claim.get(claim.id, []),
                )
            )
        gaps = [
            EvidenceGapView(
                id=item.id,
                disclosure_draft_id=item.disclosure_draft_id,
                disclosure_claim_id=item.disclosure_claim_id,
                requirement_id=item.requirement_id,
                code=item.code,
                severity=item.severity,
                status=item.status,
                message=item.message,
                details=item.details,
                resolved_at=item.resolved_at,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in aggregate.gaps
        ]
        measurement_id = self._summary_uuid(aggregate.draft.validation_summary, "measurement_id")
        if measurement_id is None:
            raise AssuranceValidationError(
                "The persisted draft does not identify its source measurement."
            )
        return DisclosureDraftView(
            id=aggregate.draft.id,
            company_id=aggregate.draft.company_id,
            standard=_standard_summary(aggregate.standard),
            site_id=aggregate.draft.site_id,
            reporting_period_id=aggregate.draft.reporting_period_id,
            measurement_id=measurement_id,
            agent_run_id=aggregate.draft.agent_run_id,
            ledger_event_id=aggregate.draft.ledger_event_id,
            version=aggregate.draft.version,
            title=aggregate.draft.title,
            narrative_template=aggregate.draft.narrative_template,
            rendered_text=aggregate.draft.rendered_text,
            context_hash=aggregate.draft.context_hash,
            payload_hash=aggregate.draft.payload_hash,
            status=aggregate.draft.status,
            validation_summary=aggregate.draft.validation_summary,
            invalidated_at=aggregate.draft.invalidated_at,
            created_at=aggregate.draft.created_at,
            updated_at=aggregate.draft.updated_at,
            claims=claims,
            gaps=gaps,
            approval=_approval_summary(aggregate.approval),
        )

    def _preview_payload(
        self,
        aggregate: DraftAggregate,
        *,
        context_hash: str,
        analysis_signature: str,
        approval_expires_at: datetime | None,
        approval_eligible: bool,
    ) -> dict[str, Any]:
        view = self._draft_view(aggregate)
        bindings = [self._fact_binding_view(item) for item in aggregate.fact_bindings]
        return {
            "target_type": "disclosure_draft",
            "target_id": aggregate.draft.id,
            "analysis_signature": analysis_signature,
            "context_hash": context_hash,
            "company_id": aggregate.draft.company_id,
            "standard": {
                "id": aggregate.standard.id,
                "code": aggregate.standard.code,
                "version": aggregate.standard.version,
            },
            "site_id": aggregate.draft.site_id,
            "reporting_period_id": aggregate.draft.reporting_period_id,
            "measurement_id": view.measurement_id,
            "draft_version": aggregate.draft.version,
            "title": aggregate.draft.title,
            "rendered_text": aggregate.draft.rendered_text,
            "claims": [item.model_dump(mode="python") for item in view.claims],
            "gaps": [item.model_dump(mode="python") for item in view.gaps],
            "fact_bindings": [item.model_dump(mode="python") for item in bindings],
            "validation_method": VALIDATION_METHOD_ID,
            "retrieval_method": RETRIEVAL_METHOD_ID,
            "embedding_model": EMBEDDING_MODEL_ID,
            "approval_eligible": approval_eligible,
            "approval_expires_at": approval_expires_at,
            "disclaimer": DISCLAIMER,
        }

    @staticmethod
    def _render_draft(aggregate: DraftAggregate) -> str:
        lines = []
        for claim in aggregate.claims:
            requirement = next(
                (item for item in aggregate.requirements if item.id == claim.requirement_id),
                None,
            )
            if claim.rendered_text is not None and claim.support_status == "supported":
                lines.append(claim.rendered_text)
            else:
                code = requirement.requirement_code if requirement else "UNKNOWN"
                lines.append(f"Unsupported claim [{code}]: evidence requirements were not met.")
        lines.append(DISCLAIMER)
        return "\n\n".join(lines)

    @staticmethod
    def _unique_evidence(items: list[EvidenceRecord]) -> list[EvidenceRecord]:
        by_id = {item.evidence.id: item for item in items}
        return sorted(
            by_id.values(),
            key=lambda item: (item.document.checksum, item.evidence.locator, str(item.evidence.id)),
        )

    @staticmethod
    def _claim_codes(aggregate: DraftAggregate, *, supported: bool) -> set[str]:
        requirements = {item.id: item.requirement_code for item in aggregate.requirements}
        return {
            requirements[claim.requirement_id]
            for claim in aggregate.claims
            if claim.requirement_id in requirements
            and (claim.support_status == "supported") is supported
        }

    @staticmethod
    def _requirement_is_required(
        requirement_id: UUID | None,
        requirements: list[DisclosureRequirement] | tuple[DisclosureRequirement, ...],
    ) -> bool:
        return any(item.id == requirement_id and item.is_required for item in requirements)

    @staticmethod
    def _summary_uuid(summary: dict[str, Any], key: str) -> UUID | None:
        value = summary.get(key)
        try:
            return value if isinstance(value, UUID) else UUID(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fact_binding_view(binding: FactBinding) -> FactBindingView:
        return FactBindingView(
            id=binding.id,
            artifact_type=binding.artifact_type,
            artifact_id=binding.artifact_id,
            agent_run_id=binding.agent_run_id,
            ledger_event_id=binding.ledger_event_id,
            evidence_item_id=binding.evidence_item_id,
            placeholder=binding.placeholder,
            value_snapshot=binding.value_snapshot,
            display_value=binding.display_value,
            unit=binding.unit,
            context_hash=binding.context_hash,
            binding_hash=binding.binding_hash,
            created_at=binding.created_at,
        )

    def _validation_result(
        self,
        aggregate: DraftAggregate,
        *,
        idempotent: bool,
    ) -> DisclosureDraftValidationResult:
        view = self._draft_view(aggregate)
        supported = sum(item.support_status == "supported" for item in view.claims)
        partial = sum(item.support_status == "partially_supported" for item in view.claims)
        unsupported = len(view.claims) - supported - partial
        terminal = aggregate.draft.validation_summary.get("terminal_state")
        if terminal not in {
            "success",
            "approval_required",
            "unsupported",
            "validation_failed",
            "stale",
            "no_data",
        }:
            terminal = "unsupported" if aggregate.draft.status == "blocked" else "success"
        return DisclosureDraftValidationResult(
            draft=view,
            terminal_state=terminal,
            supported_claims=supported,
            partially_supported_claims=partial,
            unsupported_claims=unsupported,
            open_gaps=sum(item.status == "open" for item in view.gaps),
            idempotent=idempotent,
        )

    async def _aggregate_is_stale(self, aggregate: DraftAggregate) -> bool:
        measurement_id = self._summary_uuid(aggregate.draft.validation_summary, "measurement_id")
        requested_by = self._summary_uuid(aggregate.draft.validation_summary, "requested_by")
        if measurement_id is None or requested_by is None:
            return True
        dependencies = await self.repository.load_draft_dependencies(
            company_id=aggregate.draft.company_id,
            standard_id=aggregate.draft.standard_id,
            site_id=aggregate.draft.site_id,
            reporting_period_id=aggregate.draft.reporting_period_id,
            measurement_id=measurement_id,
            requested_by=requested_by,
            agent_run_id=aggregate.draft.agent_run_id,
        )
        if dependencies is None or dependencies.measurement.ledger_event_id is None:
            return True
        current_requirements = await self.repository.list_requirements(
            company_id=aggregate.draft.company_id,
            standard_id=aggregate.draft.standard_id,
            active_only=True,
        )
        try:
            self._validate_draft_dependencies(dependencies, current_requirements)
            current_plans = [
                await self._plan_claim(aggregate.draft, dependencies, requirement)
                for requirement in current_requirements
            ]
        except AssuranceValidationError:
            return True
        current_evidence = self._unique_evidence(
            [item for plan in current_plans for item in plan.evidence]
        )
        event = await self.repository.get_ledger_event(
            company_id=aggregate.draft.company_id,
            event_id=dependencies.measurement.ledger_event_id,
        )
        current_hash = _hash(
            _source_state(
                dependencies,
                current_requirements,
                current_evidence,
                event,
            )
        )
        if current_hash != aggregate.draft.validation_summary.get("source_hash"):
            return True
        current_context_hash = _hash(
            {
                "base_context": _base_context(dependencies, current_requirements),
                "source_hash": current_hash,
            }
        )
        if current_context_hash != aggregate.draft.context_hash:
            return True
        for binding in aggregate.fact_bindings:
            material = {
                "artifact_type": binding.artifact_type,
                "artifact_id": binding.artifact_id,
                "placeholder": binding.placeholder,
                "ledger_event_id": binding.ledger_event_id,
                "evidence_item_id": binding.evidence_item_id,
                "value_snapshot": binding.value_snapshot,
                "display_value": binding.display_value,
                "unit": binding.unit,
                "context_hash": binding.context_hash,
            }
            if binding.binding_hash != _hash(material):
                return True
        return False

    async def _invalidate_draft(
        self,
        aggregate: DraftAggregate,
        *,
        actor_id: UUID,
        trace_id: str | None,
    ) -> DisclosureDraftValidationResult:
        now = datetime.now(UTC)
        aggregate.draft.status = "invalidated"
        aggregate.draft.invalidated_at = now
        summary = dict(aggregate.draft.validation_summary)
        summary.update(
            {
                "state": "invalidated",
                "terminal_state": "stale",
                "invalidated_at": now.isoformat(),
                "reason": "upstream_source_changed",
            }
        )
        aggregate.draft.validation_summary = summary
        if aggregate.approval is not None and aggregate.approval.status == "pending":
            aggregate.approval.status = "invalidated"
        event = await append_ledger_event(
            self.session,
            company_id=aggregate.draft.company_id,
            event_type="disclosure_draft_invalidated",
            entity_type="disclosure_draft",
            entity_id=aggregate.draft.id,
            payload={
                "draft_id": aggregate.draft.id,
                "previous_payload_hash": aggregate.draft.payload_hash,
                "reason": "upstream_source_changed",
                "invalidated_at": now,
            },
            analysis_signature=aggregate.draft.context_hash,
            created_by=actor_id,
        )
        if aggregate.draft.ledger_event_id is not None:
            await append_lineage_edge(
                self.session,
                company_id=aggregate.draft.company_id,
                parent_event_id=aggregate.draft.ledger_event_id,
                child_event_id=event.id,
                relationship_type="invalidated_by",
                metadata={"reason": "upstream_source_changed"},
            )
        aggregate.draft.ledger_event_id = event.id
        self.session.add(
            AuditLog(
                company_id=aggregate.draft.company_id,
                actor_id=actor_id,
                agent_run_id=aggregate.draft.agent_run_id,
                action="assurance.draft_invalidated",
                entity_type="disclosure_draft",
                entity_id=aggregate.draft.id,
                trace_id=trace_id,
                details={"reason": "upstream_source_changed"},
            )
        )
        await self.session.commit()
        refreshed = await self.repository.load_draft_aggregate(
            company_id=aggregate.draft.company_id,
            draft_id=aggregate.draft.id,
        )
        if refreshed is None:
            raise AssuranceStaleError("The invalidated draft became unavailable.")
        return self._validation_result(refreshed, idempotent=False)

    async def _invalidate_missing_dependencies(
        self,
        draft: Any,
        *,
        actor_id: UUID,
        trace_id: str | None,
    ) -> DisclosureDraftValidationResult:
        aggregate = await self.repository.load_draft_aggregate(
            company_id=draft.company_id,
            draft_id=draft.id,
        )
        if aggregate is None:
            raise AssuranceNotFoundError("The disclosure draft became unavailable.")
        return await self._invalidate_draft(
            aggregate,
            actor_id=actor_id,
            trace_id=trace_id,
        )
