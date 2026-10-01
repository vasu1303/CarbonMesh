"""Pure, deterministic eligibility and ranking rules for Assurance evidence."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from types import MappingProxyType
from uuid import UUID

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SIMILARITY_QUANTUM = Decimal("0.000001")
MAX_RETRIEVAL_LIMIT = 20
DEFAULT_MIN_EVIDENCE_SIMILARITY = Decimal("0.100000")


@dataclass(frozen=True, slots=True)
class EvidenceRetrievalContext:
    company_id: UUID
    site_id: UUID
    reporting_period_id: UUID
    requirement_code: str
    allowed_evidence_types: tuple[str, ...]
    embedding_model: str
    minimum_similarity: Decimal = DEFAULT_MIN_EVIDENCE_SIMILARITY
    company_name: str | None = None
    site_name: str | None = None
    reporting_period_name: str | None = None

    def __post_init__(self) -> None:
        requirement_code = self.requirement_code.strip()
        embedding_model = self.embedding_model.strip()
        evidence_types = tuple(item.strip() for item in self.allowed_evidence_types)
        if not requirement_code:
            raise ValueError("requirement_code must not be blank")
        if not embedding_model:
            raise ValueError("embedding_model must not be blank")
        if not evidence_types or any(not item for item in evidence_types):
            raise ValueError("allowed_evidence_types must contain non-blank values")
        if len(set(evidence_types)) != len(evidence_types):
            raise ValueError("allowed_evidence_types must not contain duplicates")
        if (
            not self.minimum_similarity.is_finite()
            or self.minimum_similarity < Decimal(0)
            or self.minimum_similarity > Decimal(1)
        ):
            raise ValueError("minimum_similarity must be between 0 and 1")
        object.__setattr__(self, "requirement_code", requirement_code)
        object.__setattr__(self, "embedding_model", embedding_model)
        object.__setattr__(self, "allowed_evidence_types", evidence_types)


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    """Internal retrieval record; ``content_text`` never belongs in an API DTO."""

    evidence_id: UUID
    company_id: UUID
    source_document_id: UUID
    data_source_id: UUID
    source_site_id: UUID | None
    evidence_type: str
    locator: str
    content_text: str
    checksum: str
    source_document_checksum: str
    metadata: Mapping[str, object]
    embedding_model: str | None
    embedded_at: datetime | None
    similarity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class EvidenceValidation:
    evidence_id: UUID
    eligible: bool
    reasons: tuple[str, ...]
    similarity: Decimal


def validate_evidence_candidate(
    candidate: EvidenceCandidate,
    context: EvidenceRetrievalContext,
) -> EvidenceValidation:
    """Apply every hard filter before a candidate may be cited.

    Cosine similarity is quantized for a deterministic eligibility floor,
    ranking, and replay metadata. It is never sufficient without every hard
    tenant, context, provenance, checksum, and requirement filter.
    """

    reasons: list[str] = []
    metadata = candidate.metadata

    if candidate.company_id != context.company_id:
        reasons.append("tenant_mismatch")
    if candidate.evidence_type not in context.allowed_evidence_types:
        reasons.append("evidence_type_not_allowed")
    if candidate.embedding_model != context.embedding_model or candidate.embedded_at is None:
        reasons.append("embedding_model_mismatch")

    if not SHA256_PATTERN.fullmatch(candidate.checksum):
        reasons.append("evidence_checksum_invalid")
    elif hashlib.sha256(candidate.content_text.encode("utf-8")).hexdigest() != candidate.checksum:
        reasons.append("evidence_checksum_mismatch")
    if not SHA256_PATTERN.fullmatch(candidate.source_document_checksum):
        reasons.append("source_document_checksum_invalid")

    requirement_codes = metadata.get("requirement_codes")
    if (
        not isinstance(requirement_codes, list)
        or any(not isinstance(item, str) for item in requirement_codes)
        or context.requirement_code not in requirement_codes
    ):
        reasons.append("requirement_mismatch")

    metadata_company_id, company_id_malformed = _metadata_uuid(metadata, "company_id")
    if company_id_malformed or (
        metadata_company_id is not None and metadata_company_id != str(context.company_id)
    ):
        reasons.append("company_context_mismatch")
    if not _optional_label_matches(metadata.get("company"), context.company_name):
        reasons.append("company_context_mismatch")

    metadata_site_id, site_id_malformed = _metadata_uuid(metadata, "site_id")
    stable_site_matches = candidate.source_site_id == context.site_id or (
        metadata_site_id == str(context.site_id)
    )
    label_site_matches = _required_label_matches(metadata.get("site"), context.site_name)
    if (
        site_id_malformed
        or (not stable_site_matches and not label_site_matches)
        or not _optional_label_matches(metadata.get("site"), context.site_name)
    ):
        reasons.append("site_context_mismatch")

    metadata_period_id, period_id_malformed = _metadata_uuid(metadata, "reporting_period_id")
    stable_period_matches = metadata_period_id == str(context.reporting_period_id)
    label_period_matches = _required_label_matches(
        metadata.get("reporting_period"), context.reporting_period_name
    )
    if (
        period_id_malformed
        or (not stable_period_matches and not label_period_matches)
        or not _optional_label_matches(
            metadata.get("reporting_period"), context.reporting_period_name
        )
    ):
        reasons.append("reporting_period_context_mismatch")

    if candidate.similarity < Decimal(-1) or candidate.similarity > Decimal(1):
        reasons.append("similarity_out_of_range")
    elif _quantize_similarity(candidate.similarity) < context.minimum_similarity:
        reasons.append("similarity_below_threshold")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return EvidenceValidation(
        evidence_id=candidate.evidence_id,
        eligible=not unique_reasons,
        reasons=unique_reasons,
        similarity=_quantize_similarity(candidate.similarity),
    )


def rank_evidence_candidates(
    candidates: list[EvidenceCandidate] | tuple[EvidenceCandidate, ...],
    context: EvidenceRetrievalContext,
    *,
    limit: int = 5,
) -> tuple[EvidenceCandidate, ...]:
    """Return eligible candidates in deterministic cosine/tie-break order."""

    if isinstance(limit, bool) or not 1 <= limit <= MAX_RETRIEVAL_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_RETRIEVAL_LIMIT}")
    eligible = [
        candidate
        for candidate in candidates
        if validate_evidence_candidate(candidate, context).eligible
    ]
    eligible.sort(
        key=lambda item: (
            -_quantize_similarity(item.similarity),
            item.source_document_checksum,
            item.locator,
            str(item.evidence_id),
        )
    )
    return tuple(eligible[:limit])


def _quantize_similarity(value: Decimal) -> Decimal:
    return value.quantize(SIMILARITY_QUANTUM, rounding=ROUND_HALF_EVEN)


def _uuid_text(value: object) -> str | None:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _metadata_uuid(metadata: Mapping[str, object], key: str) -> tuple[str | None, bool]:
    if key not in metadata:
        return None, False
    value = metadata[key]
    parsed = _uuid_text(value)
    return parsed, parsed is None


def _optional_label_matches(value: object, expected: str | None) -> bool:
    if value is None or expected is None:
        return True
    return isinstance(value, str) and value.strip() == expected.strip()


def _required_label_matches(value: object, expected: str | None) -> bool:
    if expected is None:
        return False
    return isinstance(value, str) and value.strip() == expected.strip()
