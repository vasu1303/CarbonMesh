from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.assurance.schemas import (
    AssuranceAgentResult,
    AssuranceEvidencePack,
    AssuranceRequirementView,
    AssuranceStandardSummary,
    DisclosureClaimView,
    DisclosureDraftCreateRequest,
    DisclosureDraftValidateRequest,
    DisclosureDraftValidationResult,
    DisclosureDraftView,
    EvidenceGapView,
    FactBindingView,
    SafeEvidenceSummary,
)

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64


def _id(value: int) -> UUID:
    return UUID(int=value)


def test_draft_requirement_scope_rejects_duplicate_ids() -> None:
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        DisclosureDraftCreateRequest(
            company_id=_id(1),
            standard_id=_id(2),
            site_id=_id(3),
            reporting_period_id=_id(4),
            measurement_id=_id(5),
            requirement_ids=(_id(6), _id(6)),
            requested_by=_id(7),
            idempotency_key="assurance-duplicate-requirements",
        )


def _standard() -> AssuranceStandardSummary:
    return AssuranceStandardSummary(
        id=_id(1),
        company_id=_id(2),
        source_document_id=_id(3),
        code="GHG-PROTOCOL-SCOPE-2-DEMO",
        version="2026-demo-v1",
        name="Synthetic Scope 2 summary",
        jurisdiction="GLOBAL",
        description="POC draft",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )


def _claim(*, identifier: int = 20, sequence: int = 1) -> DisclosureClaimView:
    return DisclosureClaimView(
        id=_id(identifier),
        disclosure_draft_id=_id(10),
        requirement_id=_id(30 + sequence),
        requirement_code=f"REQ-{sequence}",
        fact_binding_id=None,
        ledger_event_id=None,
        sequence=sequence,
        claim_type="qualitative",
        claim_template="A synthetic claim.",
        rendered_text="A synthetic claim.",
        support_status="supported",
        confidence=Decimal("1.00000"),
        validation_details={},
        validated_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        citations=[],
    )


def _gap(*, identifier: int = 40, code: str = "missing_support") -> EvidenceGapView:
    return EvidenceGapView(
        id=_id(identifier),
        disclosure_draft_id=_id(10),
        disclosure_claim_id=None,
        requirement_id=None,
        code=code,
        severity="error",
        status="open",
        message="Synthetic support is missing.",
        details={},
        resolved_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _draft(
    *,
    claims: list[DisclosureClaimView] | None = None,
    gaps: list[EvidenceGapView] | None = None,
) -> DisclosureDraftView:
    return DisclosureDraftView(
        id=_id(10),
        company_id=_id(2),
        standard=_standard(),
        site_id=_id(4),
        reporting_period_id=_id(5),
        measurement_id=_id(6),
        agent_run_id=None,
        ledger_event_id=None,
        version=1,
        title="Q3 2026 Scope 2 draft",
        narrative_template="{fact_scope2_total}",
        rendered_text=None,
        context_hash=HASH_A,
        payload_hash=HASH_B,
        status="blocked" if gaps else "pending_approval",
        validation_summary={},
        invalidated_at=None,
        created_at=NOW,
        updated_at=NOW,
        claims=claims or [],
        gaps=gaps or [],
        approval=None,
    )


def _evidence(identifier: int = 50) -> SafeEvidenceSummary:
    return SafeEvidenceSummary(
        id=_id(identifier),
        source_document_id=_id(51),
        data_source_id=_id(52),
        source_filename="assurance-evidence.md",
        source_document_checksum=HASH_A,
        evidence_type="disclosure_support",
        locator="text:v1:chunk=0000",
        checksum=HASH_B,
        metadata={"synthetic": True},
        embedding_model="carbonmesh-hash-768-v1",
        embedded_at=NOW,
        similarity=Decimal("0.750000"),
    )


def _binding(identifier: int = 60, placeholder: str = "fact_scope2_total") -> FactBindingView:
    return FactBindingView(
        id=_id(identifier),
        artifact_type="disclosure_draft",
        artifact_id=_id(10),
        agent_run_id=_id(61),
        ledger_event_id=_id(62),
        evidence_item_id=_id(50),
        placeholder=placeholder,
        value_snapshot={"value": "519024.720000", "unit": "kgCO2e"},
        display_value="519,024.720000 kgCO2e",
        unit="kgCO2e",
        context_hash=HASH_A,
        binding_hash=HASH_B,
        created_at=NOW,
    )


def test_draft_commands_are_strict_and_normalize_bounded_text() -> None:
    request = DisclosureDraftCreateRequest(
        company_id=_id(1),
        standard_id=_id(2),
        site_id=_id(3),
        reporting_period_id=_id(4),
        measurement_id=_id(5),
        agent_run_id=None,
        requested_by=_id(6),
        idempotency_key="  assurance:create:q3  ",
        title="  Q3 Scope 2  ",
    )
    assert request.idempotency_key == "assurance:create:q3"
    assert request.title == "Q3 Scope 2"

    with pytest.raises(ValidationError):
        DisclosureDraftCreateRequest.model_validate(
            {**request.model_dump(), "unexpected": "rejected"}
        )
    with pytest.raises(ValidationError):
        DisclosureDraftValidateRequest(
            company_id=_id(1),
            requested_by=_id(6),
            expected_context_hash="not-a-hash",
            idempotency_key="validate:q3",
        )


def test_decimal_confidence_serializes_as_an_exact_json_string() -> None:
    requirement = AssuranceRequirementView(
        id=_id(1),
        standard_id=_id(2),
        metric_definition_id=None,
        requirement_code="S2-BOUNDARY",
        title="Boundary",
        description="Identify the reporting boundary.",
        sequence=1,
        claim_template="{company} reports for {site}.",
        evidence_rules={},
        minimum_confidence=Decimal("0.80000"),
        is_required=True,
        is_active=True,
    )
    assert '"minimum_confidence":"0.80000"' in requirement.model_dump_json()


def test_safe_evidence_has_no_body_field_and_rejects_secret_metadata() -> None:
    evidence = _evidence()
    payload = evidence.model_dump(mode="json")
    assert "content_text" not in payload

    with pytest.raises(ValidationError):
        SafeEvidenceSummary.model_validate({**payload, "content_text": "secret body"})
    with pytest.raises(ValidationError):
        SafeEvidenceSummary.model_validate(
            {**payload, "metadata": {"nested": {"api_key": "secret"}}}
        )


def test_draft_rejects_unordered_claims_and_gaps() -> None:
    with pytest.raises(ValidationError, match="claims must be ordered"):
        _draft(claims=[_claim(identifier=22, sequence=2), _claim(identifier=21, sequence=1)])

    first = _gap(identifier=41, code="a")
    second = _gap(identifier=40, code="b")
    with pytest.raises(ValidationError, match="gaps must be ordered"):
        _draft(gaps=[second, first])


def test_validation_result_counts_must_match_ordered_claims_and_gaps() -> None:
    draft = _draft(claims=[_claim()], gaps=[_gap()])
    result = DisclosureDraftValidationResult(
        draft=draft,
        terminal_state="unsupported",
        supported_claims=1,
        partially_supported_claims=0,
        unsupported_claims=0,
        open_gaps=1,
    )
    assert result.open_gaps == 1

    with pytest.raises(ValidationError, match="supported_claims"):
        DisclosureDraftValidationResult(
            draft=draft,
            terminal_state="unsupported",
            supported_claims=0,
            partially_supported_claims=0,
            unsupported_claims=1,
            open_gaps=1,
        )


def test_evidence_pack_is_json_safe_traceable_and_deterministically_ordered() -> None:
    pack = AssuranceEvidencePack(
        generated_at=NOW,
        company_id=_id(2),
        draft_id=_id(10),
        standard_code="GHG-PROTOCOL-SCOPE-2-DEMO",
        standard_version="2026-demo-v1",
        context_hash=HASH_A,
        payload_hash=HASH_B,
        claims=[_claim()],
        gaps=[_gap()],
        fact_bindings=[_binding()],
        evidence=[_evidence()],
    )
    payload = pack.model_dump_json()
    assert "content_text" not in payload
    assert '"display_value":"519,024.720000 kgCO2e"' in payload

    with pytest.raises(ValidationError, match="fact_bindings must be ordered"):
        AssuranceEvidencePack(
            **{
                **pack.model_dump(),
                "fact_bindings": [
                    _binding(identifier=63, placeholder="fact_z"),
                    _binding(identifier=64, placeholder="fact_a"),
                ],
            }
        )


def test_agent_result_codes_are_sorted_and_unique() -> None:
    result = AssuranceAgentResult(
        draft_id=_id(10),
        terminal_state="unsupported",
        status="blocked",
        supported_claim_codes=["S2-BOUNDARY", "S2-TOTAL"],
        unsupported_claim_codes=["ESRS-E1-TREND-LIMITED"],
        evidence_gap_codes=["missing_comparable_period"],
        context_hash=HASH_A,
        payload_hash=HASH_B,
    )
    assert result.approval_id is None

    with pytest.raises(ValidationError, match="sorted and unique"):
        AssuranceAgentResult(
            **{
                **result.model_dump(),
                "supported_claim_codes": ["S2-TOTAL", "S2-BOUNDARY"],
            }
        )
