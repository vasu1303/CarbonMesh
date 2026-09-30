from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.db.models.core import Actor, Company, ReportingPeriod, Site
from app.db.models.procurement import Supplier
from app.db.models.semantic import MetricDefinition
from app.modules.semantic.repository import SemanticRepositoryProtocol
from app.modules.semantic.schemas import (
    ContextEnvelope,
    ContextResolveRequest,
    MetricDefinitionList,
    MetricDefinitionRead,
    ResolvedActor,
    ResolvedCompany,
    ResolvedReportingPeriod,
    ResolvedSite,
    ResolvedSupplier,
)


class ContextResolutionError(ValueError):
    """Safe, typed context error suitable for an API boundary."""

    def __init__(self, code: str, message: str, *, fields: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = dict(fields or {})


class SemanticService:
    def __init__(self, repository: SemanticRepositoryProtocol) -> None:
        self._repository = repository

    async def list_metrics(
        self, company_id: UUID, *, active_only: bool = True
    ) -> MetricDefinitionList:
        company = await self._repository.get_company(company_id)
        if company is None:
            raise ContextResolutionError(
                "company_not_found",
                "The requested company does not exist.",
                fields={"company_id": "not_found"},
            )
        if not company.is_active:
            raise ContextResolutionError(
                "company_inactive",
                "The requested company is inactive.",
                fields={"company_id": "inactive"},
            )

        definitions = await self._repository.list_metrics(
            company_id,
            active_only=active_only,
        )
        items = [_metric_response(item) for item in definitions]
        return MetricDefinitionList(company_id=company_id, items=items, count=len(items))

    async def resolve_context(self, request: ContextResolveRequest) -> ContextEnvelope:
        company = await self._require_company(request.company_id)
        site = await self._require_site(request.site_id, company.id)
        reporting_period = await self._require_reporting_period(
            request.reporting_period_id,
            company.id,
        )
        metrics = await self._require_metrics(request.metric_definition_ids, company.id)
        actor = await self._require_actor(request.actor_id, company.id)
        suppliers = await self._require_suppliers(request.supplier_scope, company.id)

        unsigned_payload = {
            "company": ResolvedCompany(
                id=company.id,
                code=company.code,
                name=company.name,
                is_synthetic=company.is_synthetic,
            ),
            "site": ResolvedSite(
                id=site.id,
                code=site.code,
                name=site.name,
                country_code=site.country_code,
                timezone=site.timezone,
            ),
            "reporting_period": ResolvedReportingPeriod(
                id=reporting_period.id,
                name=reporting_period.name,
                start_date=reporting_period.start_date,
                end_date=reporting_period.end_date,
                status=reporting_period.status,
            ),
            "metrics": [_metric_response(metric) for metric in metrics],
            "workflow": request.workflow,
            "actor": (
                ResolvedActor(
                    id=actor.id,
                    display_name=actor.display_name,
                    role=actor.role,
                )
                if actor is not None
                else None
            ),
            "supplier_scope": [
                ResolvedSupplier(
                    id=supplier.id,
                    supplier_code=supplier.supplier_code,
                    name=supplier.name,
                )
                for supplier in suppliers
            ],
            "material_scope": sorted(request.material_scope),
            "constraints": request.constraints,
        }
        analysis_signature = build_analysis_signature(unsigned_payload)
        return ContextEnvelope(**unsigned_payload, analysis_signature=analysis_signature)

    async def _require_company(self, company_id: UUID) -> Company:
        company = await self._repository.get_company(company_id)
        if company is None:
            raise ContextResolutionError(
                "company_not_found",
                "The requested company does not exist.",
                fields={"company_id": "not_found"},
            )
        if not company.is_active:
            raise ContextResolutionError(
                "company_inactive",
                "The requested company is inactive.",
                fields={"company_id": "inactive"},
            )
        return company

    async def _require_site(self, site_id: UUID, company_id: UUID) -> Site:
        site = await self._repository.get_site(company_id, site_id)
        if site is None:
            raise ContextResolutionError(
                "site_not_found",
                "The requested site does not exist.",
                fields={"site_id": "not_found"},
            )
        if site.company_id != company_id:
            raise ContextResolutionError(
                "context_relationship_mismatch",
                "The requested site does not belong to the company.",
                fields={"site_id": "company_mismatch"},
            )
        if not site.is_active:
            raise ContextResolutionError(
                "site_inactive",
                "The requested site is inactive.",
                fields={"site_id": "inactive"},
            )
        return site

    async def _require_reporting_period(
        self,
        reporting_period_id: UUID,
        company_id: UUID,
    ) -> ReportingPeriod:
        reporting_period = await self._repository.get_reporting_period(
            company_id, reporting_period_id
        )
        if reporting_period is None:
            raise ContextResolutionError(
                "reporting_period_not_found",
                "The requested reporting period does not exist.",
                fields={"reporting_period_id": "not_found"},
            )
        if reporting_period.company_id != company_id:
            raise ContextResolutionError(
                "context_relationship_mismatch",
                "The requested reporting period does not belong to the company.",
                fields={"reporting_period_id": "company_mismatch"},
            )
        return reporting_period

    async def _require_metrics(
        self,
        metric_definition_ids: Sequence[UUID],
        company_id: UUID,
    ) -> list[MetricDefinition]:
        definitions = list(
            await self._repository.get_metrics(company_id, metric_definition_ids)
        )
        by_id = {definition.id: definition for definition in definitions}
        missing = [identifier for identifier in metric_definition_ids if identifier not in by_id]
        if missing:
            raise ContextResolutionError(
                "metric_definition_not_found",
                "One or more requested metric definitions do not exist.",
                fields={"metric_definition_ids": "not_found"},
            )

        for definition in definitions:
            if definition.company_id != company_id:
                raise ContextResolutionError(
                    "context_relationship_mismatch",
                    "A requested metric definition does not belong to the company.",
                    fields={"metric_definition_ids": "company_mismatch"},
                )
            if not definition.is_active:
                raise ContextResolutionError(
                    "metric_definition_inactive",
                    "A requested metric definition is inactive.",
                    fields={"metric_definition_ids": "inactive"},
                )

        return sorted(
            definitions,
            key=lambda definition: (definition.key, definition.version, str(definition.id)),
        )

    async def _require_actor(self, actor_id: UUID | None, company_id: UUID) -> Actor | None:
        if actor_id is None:
            return None
        actor = await self._repository.get_actor(company_id, actor_id)
        if actor is None:
            raise ContextResolutionError(
                "actor_not_found",
                "The requested actor does not exist.",
                fields={"actor_id": "not_found"},
            )
        if actor.company_id != company_id:
            raise ContextResolutionError(
                "context_relationship_mismatch",
                "The requested actor does not belong to the company.",
                fields={"actor_id": "company_mismatch"},
            )
        if not actor.is_active:
            raise ContextResolutionError(
                "actor_inactive",
                "The requested actor is inactive.",
                fields={"actor_id": "inactive"},
            )
        return actor

    async def _require_suppliers(
        self,
        supplier_ids: Sequence[UUID],
        company_id: UUID,
    ) -> list[Supplier]:
        if not supplier_ids:
            return []
        suppliers = list(await self._repository.get_suppliers(company_id, supplier_ids))
        by_id = {supplier.id: supplier for supplier in suppliers}
        if any(identifier not in by_id for identifier in supplier_ids):
            raise ContextResolutionError(
                "supplier_not_found",
                "One or more requested suppliers do not exist.",
                fields={"supplier_scope": "not_found"},
            )
        for supplier in suppliers:
            if supplier.company_id != company_id:
                raise ContextResolutionError(
                    "context_relationship_mismatch",
                    "A requested supplier does not belong to the company.",
                    fields={"supplier_scope": "company_mismatch"},
                )
            if supplier.status != "active":
                raise ContextResolutionError(
                    "supplier_inactive",
                    "A requested supplier is inactive.",
                    fields={"supplier_scope": "inactive"},
                )
        return sorted(suppliers, key=lambda supplier: str(supplier.id))


def build_analysis_signature(payload: Mapping[str, Any] | BaseModel) -> str:
    """Hash canonical JSON, including exact Decimal values, for stable context binding."""

    canonical_payload = _canonicalize(payload)
    serialized = json.dumps(
        canonical_payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _canonicalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Canonical payload decimals must be finite.")
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Canonical payload numbers must be finite.")
        return _canonicalize(Decimal(str(value)))
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"Unsupported canonical payload value: {type(value).__name__}")


def _metric_response(definition: MetricDefinition) -> MetricDefinitionRead:
    return MetricDefinitionRead(
        id=definition.id,
        key=definition.key,
        version=definition.version,
        name=definition.name,
        canonical_unit=definition.canonical_unit,
        dimensions=definition.dimensions,
        handler=definition.handler,
        method_version=definition.method_version,
        description=definition.description,
    )
