from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
IdempotencyKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
DecimalZeroToOne = Annotated[Decimal, Field(ge=0, le=1)]

DraftStatus = Literal[
    "draft",
    "validating",
    "blocked",
    "pending_approval",
    "approved",
    "rejected",
    "invalidated",
]
ClaimType = Literal["numeric", "qualitative", "method", "scope"]
ClaimSupportStatus = Literal[
    "supported",
    "partially_supported",
    "unsupported",
    "citation_invalid",
    "context_mismatch",
    "stale_fact",
    "policy_blocked",
]
CitationValidationStatus = Literal["valid", "invalid", "stale", "context_mismatch"]
GapSeverity = Literal["info", "warning", "error"]
GapStatus = Literal["open", "resolved", "waived"]
AssuranceTerminalState = Literal[
    "success",
    "approval_required",
    "unsupported",
    "validation_failed",
    "stale",
    "no_data",
]


class AssuranceSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        hide_input_in_errors=True,
    )


class AssuranceRequirementView(AssuranceSchema):
    id: UUID
    standard_id: UUID
    metric_definition_id: UUID | None
    requirement_code: Annotated[str, Field(min_length=1, max_length=100)]
    title: Annotated[str, Field(min_length=1, max_length=255)]
    description: NonEmptyText
    sequence: int = Field(gt=0)
    claim_template: NonEmptyText
    evidence_rules: dict[str, Any]
    minimum_confidence: DecimalZeroToOne
    is_required: bool
    is_active: bool


class AssuranceStandardSummary(AssuranceSchema):
    id: UUID
    company_id: UUID
    source_document_id: UUID | None
    code: Annotated[str, Field(min_length=1, max_length=100)]
    version: Annotated[str, Field(min_length=1, max_length=50)]
    name: Annotated[str, Field(min_length=1, max_length=255)]
    jurisdiction: Annotated[str | None, Field(max_length=100)] = None
    description: str | None = None
    effective_from: date
    effective_to: date | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AssuranceStandardDetail(AssuranceStandardSummary):
    template: dict[str, Any]
    requirements: list[AssuranceRequirementView]

    @field_validator("requirements")
    @classmethod
    def requirements_are_ordered(
        cls, value: list[AssuranceRequirementView]
    ) -> list[AssuranceRequirementView]:
        keys = [(item.sequence, str(item.id)) for item in value]
        if keys != sorted(keys):
            raise ValueError("requirements must be ordered by sequence and id")
        if len({item.id for item in value}) != len(value):
            raise ValueError("requirements must not contain duplicate ids")
        return value


class AssuranceStandardListResponse(AssuranceSchema):
    items: list[AssuranceStandardDetail]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class DisclosureDraftCreateRequest(AssuranceSchema):
    company_id: UUID
    standard_id: UUID
    site_id: UUID
    reporting_period_id: UUID
    measurement_id: UUID
    requirement_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=100)
    agent_run_id: UUID | None = None
    requested_by: UUID
    idempotency_key: IdempotencyKey
    title: Annotated[str | None, Field(min_length=1, max_length=255)] = None

    @field_validator("requirement_ids")
    @classmethod
    def requirement_scope_is_unique(
        cls, value: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        if len(value) != len(set(value)):
            raise ValueError("requirement_ids must not contain duplicates")
        return value

    @field_validator("title", mode="before")
    @classmethod
    def normalize_optional_title(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class DisclosureDraftValidateRequest(AssuranceSchema):
    company_id: UUID
    requested_by: UUID
    expected_context_hash: Sha256 | None = None
    idempotency_key: IdempotencyKey


_UNSAFE_METADATA_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "content_text",
        "credential",
        "document_body",
        "password",
        "prompt",
        "raw_content",
        "secret",
        "token",
    }
)


def _contains_unsafe_metadata(value: object) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).casefold() in _UNSAFE_METADATA_KEYS:
                return True
            if _contains_unsafe_metadata(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_unsafe_metadata(item) for item in value)
    return False


class SafeEvidenceSummary(AssuranceSchema):
    """Evidence identity and provenance safe for an API response.

    Raw evidence bodies deliberately have no field in this contract.
    """

    id: UUID
    source_document_id: UUID
    data_source_id: UUID
    source_filename: Annotated[str, Field(min_length=1, max_length=255)]
    source_document_checksum: Sha256
    evidence_type: Annotated[str, Field(min_length=1, max_length=40)]
    locator: Annotated[str, Field(min_length=1, max_length=500)]
    checksum: Sha256
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding_model: Annotated[str | None, Field(max_length=100)] = None
    embedded_at: datetime | None = None
    similarity: Decimal | None = Field(default=None, ge=-1, le=1)

    @field_validator("metadata")
    @classmethod
    def metadata_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _contains_unsafe_metadata(value):
            raise ValueError("evidence metadata contains a non-public field")
        return value

    @field_validator("embedded_at")
    @classmethod
    def embedded_timestamp_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("embedded_at must include a UTC offset")
        return value.astimezone(UTC)


class ClaimCitationView(AssuranceSchema):
    id: UUID
    disclosure_claim_id: UUID
    ledger_event_id: UUID | None
    evidence_item_id: UUID | None
    locator: str | None
    validation_status: CitationValidationStatus
    validation_details: dict[str, Any]
    evidence: SafeEvidenceSummary | None = None
    created_at: datetime

    @model_validator(mode="after")
    def source_is_present(self) -> ClaimCitationView:
        if self.ledger_event_id is None and self.evidence_item_id is None:
            raise ValueError("a citation must identify a ledger event or evidence item")
        return self


class DisclosureClaimView(AssuranceSchema):
    id: UUID
    disclosure_draft_id: UUID
    requirement_id: UUID | None
    requirement_code: str | None = None
    fact_binding_id: UUID | None
    ledger_event_id: UUID | None
    sequence: int = Field(gt=0)
    claim_type: ClaimType
    claim_template: NonEmptyText
    rendered_text: str | None
    support_status: ClaimSupportStatus
    confidence: DecimalZeroToOne
    validation_details: dict[str, Any]
    validated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    citations: list[ClaimCitationView] = Field(default_factory=list)

    @field_validator("citations")
    @classmethod
    def citations_are_ordered(cls, value: list[ClaimCitationView]) -> list[ClaimCitationView]:
        keys = [(item.created_at, str(item.id)) for item in value]
        if keys != sorted(keys):
            raise ValueError("citations must be ordered by creation time and id")
        if len({item.id for item in value}) != len(value):
            raise ValueError("citations must not contain duplicate ids")
        return value


class EvidenceGapView(AssuranceSchema):
    id: UUID
    disclosure_draft_id: UUID
    disclosure_claim_id: UUID | None
    requirement_id: UUID | None
    code: Annotated[str, Field(min_length=1, max_length=100)]
    severity: GapSeverity
    status: GapStatus
    message: NonEmptyText
    details: dict[str, Any]
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalPreviewSummary(AssuranceSchema):
    id: UUID
    target_type: Literal["disclosure_draft"]
    target_id: UUID
    status: Literal["pending", "approved", "rejected", "invalidated", "expired"]
    preview_hash: Sha256
    analysis_signature: Sha256
    context_hash: Sha256 | None
    idempotency_key: str
    expires_at: datetime
    created_at: datetime


class DisclosureDraftView(AssuranceSchema):
    id: UUID
    company_id: UUID
    standard: AssuranceStandardSummary
    site_id: UUID
    reporting_period_id: UUID
    measurement_id: UUID
    agent_run_id: UUID | None
    ledger_event_id: UUID | None
    version: int = Field(ge=1)
    title: Annotated[str, Field(min_length=1, max_length=255)]
    narrative_template: NonEmptyText
    rendered_text: str | None
    context_hash: Sha256
    payload_hash: Sha256
    status: DraftStatus
    validation_summary: dict[str, Any]
    invalidated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    claims: list[DisclosureClaimView] = Field(default_factory=list)
    gaps: list[EvidenceGapView] = Field(default_factory=list)
    approval: ApprovalPreviewSummary | None = None

    @field_validator("claims")
    @classmethod
    def claims_are_ordered(cls, value: list[DisclosureClaimView]) -> list[DisclosureClaimView]:
        keys = [(item.sequence, str(item.id)) for item in value]
        if keys != sorted(keys):
            raise ValueError("claims must be ordered by sequence and id")
        if len({item.id for item in value}) != len(value):
            raise ValueError("claims must not contain duplicate ids")
        return value

    @field_validator("gaps")
    @classmethod
    def gaps_are_ordered(cls, value: list[EvidenceGapView]) -> list[EvidenceGapView]:
        keys = [(item.created_at, item.code, str(item.id)) for item in value]
        if keys != sorted(keys):
            raise ValueError("gaps must be ordered by creation time, code, and id")
        if len({item.id for item in value}) != len(value):
            raise ValueError("gaps must not contain duplicate ids")
        return value


class DisclosureDraftValidationResult(AssuranceSchema):
    draft: DisclosureDraftView
    terminal_state: AssuranceTerminalState
    supported_claims: int = Field(ge=0)
    partially_supported_claims: int = Field(ge=0)
    unsupported_claims: int = Field(ge=0)
    open_gaps: int = Field(ge=0)
    idempotent: bool = False

    @model_validator(mode="after")
    def counts_match_draft(self) -> DisclosureDraftValidationResult:
        statuses = [item.support_status for item in self.draft.claims]
        if self.supported_claims != statuses.count("supported"):
            raise ValueError("supported_claims does not match the ordered claims")
        if self.partially_supported_claims != statuses.count("partially_supported"):
            raise ValueError("partially_supported_claims does not match the ordered claims")
        unsupported = len(statuses) - self.supported_claims - self.partially_supported_claims
        if self.unsupported_claims != unsupported:
            raise ValueError("unsupported_claims does not match the ordered claims")
        if self.open_gaps != sum(item.status == "open" for item in self.draft.gaps):
            raise ValueError("open_gaps does not match the ordered evidence gaps")
        return self


class FactBindingView(AssuranceSchema):
    """Safe immutable snapshot binding a rendered placeholder to ledger truth."""

    id: UUID
    artifact_type: Annotated[str, Field(min_length=1, max_length=100)]
    artifact_id: UUID | None
    agent_run_id: UUID | None
    ledger_event_id: UUID
    evidence_item_id: UUID | None
    placeholder: Annotated[
        str,
        StringConstraints(pattern=r"^fact_[a-z0-9_]+$", max_length=150),
    ]
    value_snapshot: dict[str, Any]
    display_value: Annotated[str, Field(min_length=1, max_length=255)]
    unit: Annotated[str | None, Field(max_length=50)]
    context_hash: Sha256 | None
    binding_hash: Sha256 | None
    created_at: datetime


class AssuranceEvidencePack(AssuranceSchema):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: datetime
    company_id: UUID
    draft_id: UUID
    standard_code: str
    standard_version: str
    context_hash: Sha256
    payload_hash: Sha256
    claims: list[DisclosureClaimView]
    gaps: list[EvidenceGapView]
    fact_bindings: list[FactBindingView]
    evidence: list[SafeEvidenceSummary]
    disclaimer: Literal["POC draft; not an assurance opinion or filing."] = (
        "POC draft; not an assurance opinion or filing."
    )

    @field_validator("generated_at")
    @classmethod
    def generated_timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("generated_at must include a UTC offset")
        return value.astimezone(UTC)

    @field_validator("evidence")
    @classmethod
    def evidence_is_ordered(cls, value: list[SafeEvidenceSummary]) -> list[SafeEvidenceSummary]:
        keys = [(item.source_document_checksum, item.locator, str(item.id)) for item in value]
        if keys != sorted(keys):
            raise ValueError("evidence must be ordered by document checksum, locator, and id")
        if len({item.id for item in value}) != len(value):
            raise ValueError("evidence must not contain duplicate ids")
        return value

    @field_validator("fact_bindings")
    @classmethod
    def fact_bindings_are_ordered(cls, value: list[FactBindingView]) -> list[FactBindingView]:
        keys = [(item.placeholder, str(item.id)) for item in value]
        if keys != sorted(keys):
            raise ValueError("fact_bindings must be ordered by placeholder and id")
        if len({item.id for item in value}) != len(value):
            raise ValueError("fact_bindings must not contain duplicate ids")
        return value


class AssuranceAgentResult(AssuranceSchema):
    draft_id: UUID
    terminal_state: AssuranceTerminalState
    status: DraftStatus
    supported_claim_codes: list[str] = Field(default_factory=list)
    unsupported_claim_codes: list[str] = Field(default_factory=list)
    evidence_gap_codes: list[str] = Field(default_factory=list)
    approval_id: UUID | None = None
    context_hash: Sha256
    payload_hash: Sha256
    idempotent: bool = False

    @field_validator(
        "supported_claim_codes",
        "unsupported_claim_codes",
        "evidence_gap_codes",
    )
    @classmethod
    def codes_are_sorted_and_unique(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)):
            raise ValueError("agent result codes must be sorted and unique")
        return value
