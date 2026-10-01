from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.assurance.retrieval import (
    EvidenceCandidate,
    EvidenceRetrievalContext,
    rank_evidence_candidates,
    validate_evidence_candidate,
)

COMPANY_ID = UUID(int=1)
SITE_ID = UUID(int=2)
PERIOD_ID = UUID(int=3)
MODEL_ID = "carbonmesh-hash-768-v1"
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _checksum(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _context() -> EvidenceRetrievalContext:
    return EvidenceRetrievalContext(
        company_id=COMPANY_ID,
        site_id=SITE_ID,
        reporting_period_id=PERIOD_ID,
        requirement_code="S2-BOUNDARY",
        allowed_evidence_types=("disclosure_support",),
        embedding_model=MODEL_ID,
        company_name="Maverick Manufacturing (synthetic)",
        site_name="Plant B (synthetic)",
        reporting_period_name="Q3 2026",
    )


def _candidate(
    *,
    identifier: int = 10,
    content: str = "Plant B is the reporting boundary for Q3 2026.",
    similarity: str = "0.800000",
    locator: str = "text:v1:chunk=0000",
    document_checksum: str | None = None,
) -> EvidenceCandidate:
    return EvidenceCandidate(
        evidence_id=UUID(int=identifier),
        company_id=COMPANY_ID,
        source_document_id=UUID(int=20),
        data_source_id=UUID(int=21),
        source_site_id=SITE_ID,
        evidence_type="disclosure_support",
        locator=locator,
        content_text=content,
        checksum=_checksum(content),
        source_document_checksum=document_checksum or ("a" * 64),
        metadata={
            "company_id": str(COMPANY_ID),
            "site_id": str(SITE_ID),
            "reporting_period_id": str(PERIOD_ID),
            "company": "Maverick Manufacturing (synthetic)",
            "site": "Plant B (synthetic)",
            "reporting_period": "Q3 2026",
            "requirement_codes": ["S2-BOUNDARY", "S2-TOTAL"],
        },
        embedding_model=MODEL_ID,
        embedded_at=NOW,
        similarity=Decimal(similarity),
    )


def test_candidate_with_exact_tenant_context_requirement_and_checksum_is_eligible() -> None:
    validation = validate_evidence_candidate(_candidate(), _context())
    assert validation.eligible is True
    assert validation.reasons == ()
    assert validation.similarity == Decimal("0.800000")


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (replace(_candidate(), company_id=UUID(int=99)), "tenant_mismatch"),
        (replace(_candidate(), evidence_type="supplier_document"), "evidence_type_not_allowed"),
        (replace(_candidate(), embedding_model="other-model"), "embedding_model_mismatch"),
        (replace(_candidate(), checksum="b" * 64), "evidence_checksum_mismatch"),
        (
            replace(_candidate(), metadata={"requirement_codes": ["OTHER"]}),
            "requirement_mismatch",
        ),
        (
            replace(
                _candidate(),
                source_site_id=UUID(int=98),
                metadata={
                    **dict(_candidate().metadata),
                    "site_id": str(UUID(int=98)),
                    "site": "Another site",
                },
            ),
            "site_context_mismatch",
        ),
        (
            replace(
                _candidate(),
                metadata={
                    **dict(_candidate().metadata),
                    "reporting_period_id": str(UUID(int=97)),
                    "reporting_period": "Q2 2026",
                },
            ),
            "reporting_period_context_mismatch",
        ),
    ],
)
def test_hard_filters_fail_closed(candidate: EvidenceCandidate, reason: str) -> None:
    validation = validate_evidence_candidate(candidate, _context())
    assert validation.eligible is False
    assert reason in validation.reasons


def test_human_labels_are_an_explicit_fallback_for_the_current_demo_fixture() -> None:
    metadata = dict(_candidate().metadata)
    metadata.pop("company_id")
    metadata.pop("site_id")
    metadata.pop("reporting_period_id")
    current_fixture = replace(_candidate(), metadata=metadata)
    assert validate_evidence_candidate(current_fixture, _context()).eligible is True


def test_missing_period_identity_and_label_fails_closed() -> None:
    metadata = dict(_candidate().metadata)
    metadata.pop("reporting_period_id")
    metadata.pop("reporting_period")
    validation = validate_evidence_candidate(replace(_candidate(), metadata=metadata), _context())
    assert validation.eligible is False
    assert "reporting_period_context_mismatch" in validation.reasons


def test_matching_period_id_cannot_override_a_contradictory_period_label() -> None:
    metadata = {
        **dict(_candidate().metadata),
        "reporting_period": "Q2 2026",
    }
    validation = validate_evidence_candidate(replace(_candidate(), metadata=metadata), _context())
    assert validation.eligible is False
    assert "reporting_period_context_mismatch" in validation.reasons


@pytest.mark.parametrize(
    ("key", "reason"),
    [
        ("company_id", "company_context_mismatch"),
        ("site_id", "site_context_mismatch"),
        ("reporting_period_id", "reporting_period_context_mismatch"),
    ],
)
def test_explicit_malformed_context_ids_cannot_fall_back_to_matching_labels(
    key: str,
    reason: str,
) -> None:
    metadata = {**dict(_candidate().metadata), key: "not-a-uuid"}
    validation = validate_evidence_candidate(replace(_candidate(), metadata=metadata), _context())
    assert validation.eligible is False
    assert reason in validation.reasons


def test_similarity_below_explicit_default_or_configured_threshold_is_ineligible() -> None:
    below_default = validate_evidence_candidate(
        _candidate(similarity="0.099999"),
        _context(),
    )
    assert below_default.eligible is False
    assert "similarity_below_threshold" in below_default.reasons

    configured = replace(_context(), minimum_similarity=Decimal("0.200000"))
    below_configured = validate_evidence_candidate(
        _candidate(similarity="0.199999"),
        configured,
    )
    assert below_configured.eligible is False
    assert "similarity_below_threshold" in below_configured.reasons
    assert validate_evidence_candidate(_candidate(similarity="0.200000"), configured).eligible


def test_ranking_quantizes_scores_and_uses_stable_provenance_tie_breakers() -> None:
    first_by_checksum = _candidate(
        identifier=12,
        similarity="0.90000049",
        locator="z",
        document_checksum="a" * 64,
    )
    second_by_checksum = _candidate(
        identifier=11,
        similarity="0.90000040",
        locator="a",
        document_checksum="b" * 64,
    )
    clearly_lower = _candidate(identifier=10, similarity="0.700000")

    ranked = rank_evidence_candidates(
        [clearly_lower, second_by_checksum, first_by_checksum],
        _context(),
        limit=3,
    )
    assert [item.evidence_id for item in ranked] == [
        first_by_checksum.evidence_id,
        second_by_checksum.evidence_id,
        clearly_lower.evidence_id,
    ]


def test_invalid_higher_scoring_candidate_is_removed_before_ranking() -> None:
    wrong_tenant = replace(
        _candidate(identifier=11, similarity="0.990000"),
        company_id=UUID(int=99),
    )
    eligible = _candidate(identifier=12, similarity="0.500000")
    ranked = rank_evidence_candidates([wrong_tenant, eligible], _context())
    assert ranked == (eligible,)


@pytest.mark.parametrize("limit", [0, 21, True])
def test_retrieval_limit_is_explicitly_bounded(limit: int) -> None:
    with pytest.raises(ValueError, match="limit must be between"):
        rank_evidence_candidates([_candidate()], _context(), limit=limit)


def test_context_rejects_blank_or_duplicate_retrieval_policy_values() -> None:
    with pytest.raises(ValueError, match="embedding_model"):
        replace(_context(), embedding_model=" ")
    with pytest.raises(ValueError, match="duplicates"):
        replace(
            _context(),
            allowed_evidence_types=("disclosure_support", "disclosure_support"),
        )
