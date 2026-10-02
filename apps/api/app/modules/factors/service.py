from __future__ import annotations

import hashlib
import re
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import EmissionFactor
from app.db.models.core import AuditLog
from app.db.models.ledger import LedgerEvent, LedgerEventEvidence
from app.modules.factors.repository import FactorRepository
from app.modules.factors.schemas import (
    FactorFields,
    FactorListResponse,
    FactorRegisterRequest,
    FactorRegisterResponse,
    FactorView,
)
from app.modules.measurement.domain import sha256_payload


class FactorError(Exception):
    def __init__(self, message: str, *, code: str, status_code: int) -> None:
        self.message, self.code, self.status_code = message, code, status_code
        super().__init__(message)


def _view(factor: EmissionFactor) -> FactorView:
    return FactorView.model_validate({key: getattr(factor, key) for key in FactorView.model_fields})


class FactorService:
    def __init__(self, session: AsyncSession) -> None:
        self.session, self.repository = session, FactorRepository(session)

    async def register(
        self, request: FactorRegisterRequest, *, trace_id: str | None = None
    ) -> FactorRegisterResponse:
        repo = self.repository
        try:
            if await repo.lock_company(request.company_id) is None:
                raise FactorError(
                    "The company is unavailable.", code="factor_context_not_found", status_code=404
                )
            actor = await repo.actor(request.company_id, request.actor_id)
            if actor is None or actor.role not in {"sustainability_analyst", "system"}:
                raise FactorError(
                    "An active sustainability analyst must register a factor.",
                    code="factor_registration_forbidden",
                    status_code=403,
                )
            payload = request.model_dump(mode="json")
            existing = await repo.find(
                request.company_id, request.factor_code, request.version, request.geography
            )
            if existing is not None:
                event = await repo.registration_event(request.company_id, existing.id)
                if event is None or event.payload.get("request_hash") != sha256_payload(payload):
                    raise FactorError(
                        "This factor version already identifies different content.",
                        code="factor_version_conflict",
                        status_code=409,
                    )
                result = FactorRegisterResponse(
                    factor=_view(existing), ledger_event_id=event.id, replayed=True
                )
                await self.session.commit()
                return result
            metric = await repo.metric(request.company_id, request.metric_definition_id)
            if (
                metric is None
                or metric.key != "emissions.scope3.category1"
                or metric.canonical_unit != "kgCO2e"
            ):
                raise FactorError(
                    "An active purchased-material emissions metric is required.",
                    code="factor_metric_unsupported",
                    status_code=422,
                )
            record = await repo.evidence(request.company_id, request.evidence_item_id)
            if record is None:
                raise FactorError(
                    "Factor evidence was not found in the company.",
                    code="factor_evidence_not_found",
                    status_code=404,
                )
            evidence, document, source = record
            trust = evidence.evidence_metadata.get("trust_status")
            if (
                source.status != "ready"
                or (
                    trust not in ("accepted", "verified", "synthetic")
                    and not (trust is None and source.is_synthetic)
                )
                or evidence.evidence_metadata.get("source_checksum", document.checksum)
                != document.checksum
                or evidence.evidence_metadata.get("source_version", document.version)
                != document.version
                or re.fullmatch(r"[0-9a-f]{64}", document.checksum) is None
                or hashlib.sha256(evidence.content_text.encode("utf-8")).hexdigest()
                != evidence.checksum
            ):
                raise FactorError(
                    "Factor evidence is unavailable, untrusted, or fails integrity checks.",
                    code="factor_evidence_invalid",
                    status_code=422,
                )
            factor = EmissionFactor(
                **request.model_dump(include=set(FactorFields.model_fields)), status="active"
            )
            repo.add(factor)
            await repo.flush()
            event_payload = {
                "factor_id": str(factor.id),
                "factor": payload,
                "request_hash": sha256_payload(payload),
                "evidence_checksum": evidence.checksum,
                "source_document_id": str(document.id),
                "source_document_checksum": document.checksum,
                "registration_method": "evidence-backed-material-factor-v1",
            }
            event = LedgerEvent(
                company_id=request.company_id,
                event_type="factor.registered",
                entity_type="emission_factor",
                entity_id=factor.id,
                payload=event_payload,
                payload_hash=sha256_payload(event_payload),
                created_by=request.actor_id,
            )
            repo.add(event)
            await repo.flush()
            repo.add(
                LedgerEventEvidence(
                    company_id=request.company_id,
                    ledger_event_id=event.id,
                    evidence_item_id=evidence.id,
                    relevance="Registered emission factor source",
                )
            )
            repo.add(
                AuditLog(
                    company_id=request.company_id,
                    actor_id=request.actor_id,
                    action="factor.registered",
                    entity_type="emission_factor",
                    entity_id=factor.id,
                    trace_id=trace_id,
                    details={
                        "ledger_event_id": str(event.id),
                        "request_hash": sha256_payload(payload),
                    },
                )
            )
            result = FactorRegisterResponse(
                factor=_view(factor), ledger_event_id=event.id, replayed=False
            )
            await self.session.commit()
            return result
        except FactorError:
            await self.session.rollback()
            raise
        except SQLAlchemyError as error:
            await self.session.rollback()
            raise FactorError(
                "Factor storage is temporarily unavailable.",
                code="factor_storage_unavailable",
                status_code=503,
            ) from error
        except BaseException:
            await self.session.rollback()
            raise

    async def list(
        self,
        *,
        company_id: UUID,
        material_code: str | None = None,
        product_code: str | None = None,
        active: bool | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> FactorListResponse:
        try:
            records, total = await self.repository.list(
                company_id=company_id,
                material_code=material_code,
                product_code=product_code,
                active=active,
                limit=limit,
                offset=offset,
            )
            return FactorListResponse(
                items=[_view(item) for item in records], total=total, limit=limit, offset=offset
            )
        except SQLAlchemyError as error:
            await self.session.rollback()
            raise FactorError(
                "Factor storage is temporarily unavailable.",
                code="factor_storage_unavailable",
                status_code=503,
            ) from error
