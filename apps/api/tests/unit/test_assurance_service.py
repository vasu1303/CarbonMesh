from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.db.models.ledger import FactBinding
from app.modules.assurance.errors import (
    AssuranceConflictError,
    AssuranceStaleError,
    AssuranceValidationError,
)
from app.modules.assurance.repository import DraftDependencies, EvidenceRecord
from app.modules.assurance.schemas import DisclosureDraftCreateRequest
from app.modules.assurance.service import (
    AssuranceService,
    AssuranceServicePort,
    _base_context,
    _bounded_default_title,
    _hash,
    _minimum_evidence_similarity,
    _PlannedClaim,
    _source_state,
)

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _requirement(
    *,
    identifier: int = 20,
    sequence: int = 1,
    evidence_rules: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=_id(identifier),
        company_id=_id(1),
        standard_id=_id(2),
        metric_definition_id=None,
        requirement_code=f"REQ-{identifier}",
        title="Synthetic requirement",
        description="Synthetic disclosure support description.",
        sequence=sequence,
        claim_template="{company} reports for {site} in {reporting_period}.",
        evidence_rules=evidence_rules or {},
        minimum_confidence=Decimal("0.80000"),
        is_required=True,
        is_active=True,
    )


def _dependencies() -> DraftDependencies:
    company = SimpleNamespace(
        id=_id(1),
        code="MAVERICK",
        name="Maverick Manufacturing (synthetic)",
        is_synthetic=True,
        is_active=True,
    )
    site = SimpleNamespace(
        id=_id(3),
        company_id=company.id,
        name="Plant B (synthetic)",
        code="PLANT-B",
        country_code="IN",
        timezone="Asia/Kolkata",
        is_active=True,
    )
    period = SimpleNamespace(
        id=_id(4),
        company_id=company.id,
        name="Q3 2026",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 9, 30),
        status="closed",
    )
    standard = SimpleNamespace(
        id=_id(2),
        company_id=company.id,
        source_document_id=_id(5),
        code="GHG-PROTOCOL-SCOPE-2-DEMO",
        version="2026-demo-v1",
        name="Synthetic Scope 2 summary",
        jurisdiction="GLOBAL",
        description="Synthetic demonstration standard.",
        template={"kind": "scope_2_summary"},
        effective_from=date(2026, 1, 1),
        effective_to=None,
        is_active=True,
    )
    measurement = SimpleNamespace(
        id=_id(6),
        company_id=company.id,
        metric_definition_id=_id(7),
        site_id=site.id,
        reporting_period_id=period.id,
        ledger_event_id=_id(8),
        value_kgco2e=Decimal("519024.720000"),
        unit="kgCO2e",
        confidence=Decimal("0.95000"),
        status="verified",
        output_hash="a" * 64,
        formula="sum(hourly kWh * gCO2e_per_kWh / 1000)",
    )
    actor = SimpleNamespace(id=_id(9), company_id=company.id, is_active=True)
    return DraftDependencies(
        company=company,
        standard=standard,
        site=site,
        reporting_period=period,
        measurement=measurement,
        requested_by=actor,
        agent_run=None,
    )


def _evidence(*, identifier: int = 30, similarity: str = "0.200000") -> EvidenceRecord:
    content = "Plant B is the Q3 2026 reporting boundary."
    evidence = SimpleNamespace(
        id=_id(identifier),
        company_id=_id(1),
        evidence_type="disclosure_support",
        locator="text:v1:chunk=0000",
        checksum="b" * 64,
        evidence_metadata={
            "company_id": str(_id(1)),
            "site_id": str(_id(3)),
            "reporting_period_id": str(_id(4)),
            "requirement_codes": ["REQ-20"],
        },
        embedding_model="carbonmesh-hash-768-v1",
        embedding=[0.5, 0.25],
        embedded_at=NOW,
        content_text=content,
    )
    document = SimpleNamespace(
        id=_id(identifier + 1),
        filename="synthetic-evidence.md",
        content_type="text/markdown",
        version=1,
        size_bytes=len(content),
        storage_uri="fixture://synthetic-evidence.md",
        checksum="c" * 64,
        document_metadata={"synthetic": True},
    )
    source = SimpleNamespace(
        id=_id(identifier + 2),
        site_id=_id(3),
        name="Synthetic assurance evidence",
        source_type="document_upload",
        status="active",
        external_reference="fixture://assurance",
        configuration={"fixture": "assurance-v1"},
        is_synthetic=True,
    )
    return EvidenceRecord(evidence, document, source, Decimal(similarity))


class _Session:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.rollbacks = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        for index, value in enumerate(self.added, start=100):
            if isinstance(value, FactBinding) and value.id is None:
                value.id = _id(index)

    async def rollback(self) -> None:
        self.rollbacks += 1


def _service(session: _Session | None = None) -> AssuranceService:
    service = AssuranceService.__new__(AssuranceService)
    service.session = session or _Session()  # type: ignore[assignment]
    return service


def test_service_port_and_default_similarity_policy_are_explicit() -> None:
    requirement = _requirement()
    assert hasattr(AssuranceServicePort, "validate_for_agent")
    assert _minimum_evidence_similarity(requirement) == Decimal("0.100000")

    requirement.evidence_rules = {"minimum_similarity": "0.175"}
    assert _minimum_evidence_similarity(requirement) == Decimal("0.175000")

    requirement.evidence_rules = {"minimum_similarity": True}
    with pytest.raises(AssuranceValidationError, match="similarity threshold"):
        _minimum_evidence_similarity(requirement)


def test_generated_title_is_deterministic_and_bounded_to_database_column() -> None:
    title = _bounded_default_title("S" * 255, "Q" * 100)
    assert len(title) == 255
    assert title.endswith("Q" * 100)
    assert title == _bounded_default_title("S" * 255, "Q" * 100)


@pytest.mark.asyncio
async def test_retrieval_pushes_query_embedding_into_repository_ranking() -> None:
    service = _service()
    list_evidence_candidates = AsyncMock(return_value=[])
    service.repository = SimpleNamespace(  # type: ignore[attr-defined]
        list_evidence_candidates=list_evidence_candidates
    )

    result = await service._retrieve_evidence(_dependencies(), _requirement())

    assert result == ()
    query_embedding = list_evidence_candidates.await_args.kwargs["query_embedding"]
    assert len(query_embedding) == 768
    assert any(value != 0 for value in query_embedding)


@pytest.mark.parametrize(("target", "message"), [("company", "company"), ("site", "site")])
def test_dependency_validation_rejects_inactive_tenant_context(
    target: str,
    message: str,
) -> None:
    service = _service()
    dependencies = _dependencies()
    getattr(dependencies, target).is_active = False

    with pytest.raises(AssuranceValidationError, match=message):
        service._validate_draft_dependencies(dependencies, [_requirement()])


@pytest.mark.parametrize("sequences", [(0, 1), (1, 1)])
def test_dependency_validation_rejects_nonpositive_or_duplicate_sequences(
    sequences: tuple[int, int],
) -> None:
    service = _service()
    requirements = [
        _requirement(identifier=20, sequence=sequences[0]),
        _requirement(identifier=21, sequence=sequences[1]),
    ]

    with pytest.raises(AssuranceValidationError, match="unique positive"):
        service._validate_draft_dependencies(_dependencies(), requirements)


@pytest.mark.asyncio
async def test_create_draft_rechecks_idempotency_after_version_lock() -> None:
    session = _Session()
    service = _service(session)
    dependencies = _dependencies()
    requirement = _requirement()
    request = DisclosureDraftCreateRequest(
        company_id=dependencies.company.id,
        standard_id=dependencies.standard.id,
        site_id=dependencies.site.id,
        reporting_period_id=dependencies.reporting_period.id,
        measurement_id=dependencies.measurement.id,
        agent_run_id=None,
        requested_by=dependencies.requested_by.id,
        idempotency_key="assurance:create:q3",
    )
    request_hash = _hash(request.model_dump(mode="python"))
    existing = SimpleNamespace(
        id=_id(99),
        validation_summary={"create_request_hash": request_hash},
    )
    calls: list[str] = []

    async def find(**_: object) -> object | None:
        calls.append("find")
        return None if calls.count("find") == 1 else existing

    async def allocate(**_: object) -> int:
        calls.append("allocate")
        return 1

    service.repository = SimpleNamespace(  # type: ignore[attr-defined]
        find_draft_by_idempotency_key=find,
        load_draft_dependencies=AsyncMock(return_value=dependencies),
        list_requirements=AsyncMock(return_value=[requirement]),
        allocate_draft_version=allocate,
    )
    expected = SimpleNamespace(id=existing.id)
    service.get_draft = AsyncMock(return_value=expected)  # type: ignore[method-assign]

    result = await service.create_draft(request)

    assert result is expected
    assert calls == ["find", "allocate", "find"]
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_fact_snapshot_is_json_normalized_and_identical_binding_is_reused() -> None:
    session = _Session()
    service = _service(session)
    created_claims: list[dict[str, object]] = []

    async def create_claim(**values: object) -> object:
        created_claims.append(values)
        return SimpleNamespace(id=_id(200 + len(created_claims)))

    service.repository = SimpleNamespace(  # type: ignore[attr-defined]
        create_claim=create_claim,
        get_citation_by_sources=AsyncMock(return_value=None),
        create_citation=AsyncMock(),
        create_gap=AsyncMock(),
    )
    dependencies = _dependencies()
    dependencies = DraftDependencies(
        company=dependencies.company,
        standard=dependencies.standard,
        site=dependencies.site,
        reporting_period=dependencies.reporting_period,
        measurement=dependencies.measurement,
        requested_by=dependencies.requested_by,
        agent_run=SimpleNamespace(id=_id(70), actor_id=dependencies.requested_by.id),
    )
    draft = SimpleNamespace(id=_id(71), company_id=dependencies.company.id)
    snapshot = {
        "measurement_id": dependencies.measurement.id,
        "value": Decimal("519024.720000"),
        "unit": "kgCO2e",
    }
    bindings: dict[str, tuple[dict[str, object], FactBinding]] = {}

    def plan(requirement: SimpleNamespace, value: dict[str, object]) -> _PlannedClaim:
        return _PlannedClaim(
            requirement=requirement,  # type: ignore[arg-type]
            claim_type="numeric",
            rendered_text="Scope 2 emissions were 519,024.720000 kgCO2e.",
            support_status="supported",
            confidence=Decimal("0.95000"),
            validation_details={},
            evidence=(),
            fact_placeholder="fact_scope2_total",
            fact_display_value="519,024.720000 kgCO2e",
            fact_unit="kgCO2e",
            fact_value_snapshot=value,
            ledger_event_id=dependencies.measurement.ledger_event_id,
        )

    await service._persist_claim(
        draft=draft,
        dependencies=dependencies,
        plan=plan(_requirement(sequence=1), snapshot),
        context_hash="d" * 64,
        planned_bindings=bindings,  # type: ignore[arg-type]
    )
    await service._persist_claim(
        draft=draft,
        dependencies=dependencies,
        plan=plan(_requirement(identifier=21, sequence=2), dict(snapshot)),
        context_hash="d" * 64,
        planned_bindings=bindings,  # type: ignore[arg-type]
    )

    persisted = [item for item in session.added if isinstance(item, FactBinding)]
    assert len(persisted) == 1
    assert persisted[0].value_snapshot == {
        "measurement_id": str(dependencies.measurement.id),
        "value": "519024.720000",
        "unit": "kgCO2e",
    }
    assert created_claims[0]["fact_binding_id"] == created_claims[1]["fact_binding_id"]

    conflicting = {**snapshot, "value": Decimal("1.000000")}
    with pytest.raises(AssuranceValidationError, match="same placeholder"):
        await service._persist_claim(
            draft=draft,
            dependencies=dependencies,
            plan=plan(_requirement(identifier=22, sequence=3), conflicting),
            context_hash="d" * 64,
            planned_bindings=bindings,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_evidence_pack_fails_closed_before_validation_and_when_invalidated() -> None:
    service = _service()
    prevalidation = SimpleNamespace(
        draft=SimpleNamespace(status="draft", validation_summary={"state": "not_validated"}),
        claims=(),
    )
    service.repository = SimpleNamespace(  # type: ignore[attr-defined]
        load_draft_aggregate=AsyncMock(return_value=prevalidation)
    )

    with pytest.raises(AssuranceConflictError) as error:
        await service.get_evidence_pack(company_id=_id(1), draft_id=_id(2))
    assert error.value.code == "assurance_validation_required"

    invalidated = SimpleNamespace(
        draft=SimpleNamespace(
            status="invalidated",
            validation_summary={"state": "validated", "terminal_state": "stale"},
        ),
        claims=(SimpleNamespace(id=_id(3)),),
    )
    service.repository.load_draft_aggregate.return_value = invalidated
    with pytest.raises(AssuranceStaleError):
        await service.get_evidence_pack(company_id=_id(1), draft_id=_id(2))


def test_material_hashes_ignore_reseed_timestamps_but_bind_context_and_source_state() -> None:
    dependencies = _dependencies()
    requirement = _requirement()
    evidence = _evidence()
    event = SimpleNamespace(payload_hash="e" * 64)
    dependencies.company.updated_at = NOW
    dependencies.standard.updated_at = NOW
    evidence.evidence.updated_at = NOW
    evidence.document.updated_at = NOW
    evidence.source.updated_at = NOW
    baseline_context = _hash(_base_context(dependencies, [requirement]))
    baseline_source = _hash(_source_state(dependencies, [requirement], [evidence], event))

    timestamp_only = deepcopy(dependencies)
    timestamp_only.company.updated_at = datetime(2030, 1, 1, tzinfo=UTC)
    timestamp_only.standard.updated_at = datetime(2030, 1, 1, tzinfo=UTC)
    assert _hash(_base_context(timestamp_only, [requirement])) == baseline_context

    renamed = deepcopy(dependencies)
    renamed.company.name = "Renamed synthetic company"
    assert _hash(_base_context(renamed, [requirement])) != baseline_context
    changed_requirement = deepcopy(requirement)
    changed_requirement.description = "Changed retrieval query text."
    assert _hash(_base_context(dependencies, [changed_requirement])) != baseline_context

    metadata_changed = deepcopy(evidence)
    metadata_changed.evidence.evidence_metadata["policy_version"] = "v2"
    assert (
        _hash(_source_state(dependencies, [requirement], [metadata_changed], event))
        != baseline_source
    )
    embedding_changed = deepcopy(evidence)
    embedding_changed.evidence.embedding = [0.5, 0.30]
    assert (
        _hash(_source_state(dependencies, [requirement], [embedding_changed], event))
        != baseline_source
    )
    similarity_changed = deepcopy(evidence)
    similarity_changed = EvidenceRecord(
        similarity_changed.evidence,
        similarity_changed.document,
        similarity_changed.source,
        Decimal("0.210000"),
    )
    assert (
        _hash(_source_state(dependencies, [requirement], [similarity_changed], event))
        != baseline_source
    )


@pytest.mark.asyncio
async def test_staleness_reloads_active_requirements_and_current_ranked_evidence() -> None:
    service = _service()
    dependencies = _dependencies()
    requirement = _requirement()
    current_evidence = _evidence(identifier=40, similarity="0.300000")
    previously_selected = _evidence(identifier=50, similarity="0.200000")
    event = SimpleNamespace(payload_hash="f" * 64)
    source_hash = _hash(_source_state(dependencies, [requirement], [current_evidence], event))
    context_hash = _hash(
        {
            "base_context": _base_context(dependencies, [requirement]),
            "source_hash": source_hash,
        }
    )
    draft = SimpleNamespace(
        id=_id(80),
        company_id=dependencies.company.id,
        standard_id=dependencies.standard.id,
        site_id=dependencies.site.id,
        reporting_period_id=dependencies.reporting_period.id,
        agent_run_id=None,
        context_hash=context_hash,
        validation_summary={
            "measurement_id": str(dependencies.measurement.id),
            "requested_by": str(dependencies.requested_by.id),
            "source_hash": source_hash,
        },
    )
    list_requirements = AsyncMock(return_value=[requirement])
    service.repository = SimpleNamespace(  # type: ignore[attr-defined]
        load_draft_dependencies=AsyncMock(return_value=dependencies),
        list_requirements=list_requirements,
        get_ledger_event=AsyncMock(return_value=event),
    )
    service._plan_claim = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(evidence=(current_evidence,))
    )
    aggregate = SimpleNamespace(
        draft=draft,
        requirements=(_requirement(identifier=99),),
        evidence=(previously_selected,),
        fact_bindings=(),
    )

    assert await service._aggregate_is_stale(aggregate) is False
    list_requirements.assert_awaited_once_with(
        company_id=dependencies.company.id,
        standard_id=dependencies.standard.id,
        active_only=True,
    )
    service._plan_claim.assert_awaited_once_with(draft, dependencies, requirement)
