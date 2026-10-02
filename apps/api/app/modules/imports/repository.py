from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import ActivityRecord, DataQualityIssue, RawActivityRecord
from app.db.models.core import (
    Actor,
    AuditLog,
    Company,
    DataSource,
    EvidenceItem,
    ReportingPeriod,
    Site,
    SourceDocument,
)
from app.db.models.procurement import Supplier, SupplierProduct
from app.db.models.semantic import MetricDefinition


class ImportRepository:
    """Persistence operations for import use cases; no business decisions live here."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def company_exists(self, company_id: UUID) -> bool:
        statement = select(Company.id).where(Company.id == company_id, Company.is_active.is_(True))
        return (await self.session.execute(statement)).scalar_one_or_none() is not None

    async def lock_import_key(self, lock_id: int) -> None:
        await self.session.execute(select(func.pg_advisory_xact_lock(lock_id)))

    async def find_import_by_key(
        self, company_id: UUID, import_type: str, idempotency_key: str
    ) -> DataSource | None:
        statement = select(DataSource).where(
            DataSource.company_id == company_id,
            DataSource.configuration["import_type"].astext == import_type,
            DataSource.configuration["idempotency_key"].astext == idempotency_key,
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_site(self, company_id: UUID, site_id: UUID) -> Site | None:
        statement = select(Site).where(Site.company_id == company_id, Site.id == site_id)
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_reporting_period(
        self, company_id: UUID, reporting_period_id: UUID
    ) -> ReportingPeriod | None:
        statement = (
            select(ReportingPeriod)
            .where(
                ReportingPeriod.company_id == company_id,
                ReportingPeriod.id == reporting_period_id,
            )
            .with_for_update()
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_metric_definition(
        self, company_id: UUID, metric_definition_id: UUID
    ) -> MetricDefinition | None:
        statement = select(MetricDefinition).where(
            MetricDefinition.company_id == company_id,
            MetricDefinition.id == metric_definition_id,
            MetricDefinition.is_active.is_(True),
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_supplier_product(
        self, company_id: UUID, supplier_product_id: UUID
    ) -> SupplierProduct | None:
        statement = (
            select(SupplierProduct)
            .join(
                Supplier,
                and_(
                    Supplier.company_id == SupplierProduct.company_id,
                    Supplier.id == SupplierProduct.supplier_id,
                ),
            )
            .where(
                SupplierProduct.company_id == company_id,
                SupplierProduct.id == supplier_product_id,
                SupplierProduct.is_active.is_(True),
                Supplier.status == "active",
            )
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_supplier_product_by_codes(
        self, company_id: UUID, supplier_code: str, product_code: str
    ) -> SupplierProduct | None:
        statement = (
            select(SupplierProduct)
            .join(
                Supplier,
                and_(
                    Supplier.company_id == SupplierProduct.company_id,
                    Supplier.id == SupplierProduct.supplier_id,
                ),
            )
            .where(
                SupplierProduct.company_id == company_id,
                Supplier.supplier_code == supplier_code,
                SupplierProduct.product_code == product_code,
                SupplierProduct.is_active.is_(True),
                Supplier.status == "active",
            )
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_supplier(self, company_id: UUID, supplier_code: str) -> Supplier | None:
        statement = select(Supplier).where(
            Supplier.company_id == company_id,
            Supplier.supplier_code == supplier_code,
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_supplier_product_for_supplier(
        self, company_id: UUID, supplier_id: UUID, product_code: str
    ) -> SupplierProduct | None:
        statement = select(SupplierProduct).where(
            SupplierProduct.company_id == company_id,
            SupplierProduct.supplier_id == supplier_id,
            SupplierProduct.product_code == product_code,
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def find_document_by_checksum(
        self, company_id: UUID, checksum: str
    ) -> SourceDocument | None:
        statement = select(SourceDocument).where(
            SourceDocument.company_id == company_id,
            SourceDocument.checksum == checksum,
        )
        return (await self.session.execute(statement.with_for_update())).scalar_one_or_none()

    async def has_raw_rows_for_document(self, company_id: UUID, document_id: UUID) -> bool:
        statement = select(RawActivityRecord.id).where(
            RawActivityRecord.company_id == company_id,
            RawActivityRecord.source_document_id == document_id,
        ).limit(1)
        return (await self.session.execute(statement)).scalar_one_or_none() is not None

    async def create_data_source(
        self,
        *,
        company_id: UUID,
        site_id: UUID | None,
        name: str,
        source_type: str,
        external_reference: str | None,
        configuration: dict[str, Any],
        is_synthetic: bool,
    ) -> DataSource:
        source = DataSource(
            company_id=company_id,
            site_id=site_id,
            name=name,
            source_type=source_type,
            status="pending",
            external_reference=external_reference,
            configuration=configuration,
            is_synthetic=is_synthetic,
        )
        self.session.add(source)
        await self.session.flush()
        return source

    async def create_source_document(
        self,
        *,
        company_id: UUID,
        data_source_id: UUID,
        filename: str,
        content_type: str,
        checksum: str,
        size_bytes: int,
        metadata: dict[str, Any],
    ) -> SourceDocument:
        document = SourceDocument(
            company_id=company_id,
            data_source_id=data_source_id,
            filename=filename,
            content_type=content_type,
            checksum=checksum,
            version=1,
            size_bytes=size_bytes,
            storage_uri=None,
            document_metadata=metadata,
        )
        self.session.add(document)
        await self.session.flush()
        return document

    async def create_raw_activity(
        self,
        *,
        company_id: UUID,
        data_source_id: UUID,
        source_document_id: UUID,
        row_key: str,
        row_number: int,
        raw_payload: dict[str, Any],
        checksum: str,
    ) -> RawActivityRecord:
        raw_record = RawActivityRecord(
            company_id=company_id,
            data_source_id=data_source_id,
            source_document_id=source_document_id,
            row_key=row_key,
            row_number=row_number,
            raw_payload=raw_payload,
            checksum=checksum,
            import_status="pending",
        )
        self.session.add(raw_record)
        await self.session.flush()
        return raw_record

    async def create_activity(self, **values: Any) -> ActivityRecord:
        activity = ActivityRecord(**values)
        self.session.add(activity)
        await self.session.flush()
        return activity

    async def create_issue(
        self,
        *,
        company_id: UUID,
        issue_type: str,
        code: str,
        severity: str,
        message: str,
        field_name: str | None,
        details: dict[str, Any],
        raw_activity_record_id: UUID | None = None,
        activity_record_id: UUID | None = None,
    ) -> DataQualityIssue:
        now = datetime.now(UTC)
        issue = DataQualityIssue(
            company_id=company_id,
            raw_activity_record_id=raw_activity_record_id,
            activity_record_id=activity_record_id,
            issue_type=issue_type,
            code=code,
            severity=severity,
            field_name=field_name,
            message=message,
            status="open",
            details=details,
            created_at=now,
            updated_at=now,
        )
        self.session.add(issue)
        await self.session.flush()
        return issue

    async def create_supplier(
        self,
        *,
        company_id: UUID,
        supplier_code: str,
        name: str,
        country_code: str,
        metadata: dict[str, Any],
    ) -> Supplier:
        supplier = Supplier(
            company_id=company_id,
            supplier_code=supplier_code,
            name=name,
            country_code=country_code,
            status="active",
            supplier_metadata=metadata,
        )
        self.session.add(supplier)
        await self.session.flush()
        return supplier

    async def create_evidence_item(
        self,
        *,
        company_id: UUID,
        source_document_id: UUID,
        evidence_type: str,
        locator: str,
        content_text: str,
        checksum: str,
        metadata: dict[str, Any],
    ) -> EvidenceItem:
        evidence = EvidenceItem(
            company_id=company_id,
            source_document_id=source_document_id,
            evidence_type=evidence_type,
            locator=locator,
            content_text=content_text,
            checksum=checksum,
            evidence_metadata=metadata,
            embedding=None,
            embedding_model=None,
            embedded_at=None,
        )
        self.session.add(evidence)
        await self.session.flush()
        return evidence

    async def create_supplier_product(self, **values: Any) -> SupplierProduct:
        product = SupplierProduct(**values)
        self.session.add(product)
        await self.session.flush()
        return product

    async def create_audit_log(
        self,
        *,
        company_id: UUID,
        action: str,
        entity_id: UUID,
        trace_id: str,
        details: dict[str, Any],
        actor_id: UUID | None = None,
        entity_type: str = "data_source",
    ) -> AuditLog:
        audit = AuditLog(
            company_id=company_id,
            actor_id=actor_id,
            agent_run_id=None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            trace_id=trace_id,
            details=details,
        )
        self.session.add(audit)
        await self.session.flush()
        return audit

    async def get_actor(self, company_id: UUID, actor_id: UUID) -> Actor | None:
        statement = select(Actor).where(
            Actor.company_id == company_id,
            Actor.id == actor_id,
            Actor.is_active.is_(True),
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_issue_for_update(
        self, company_id: UUID, issue_id: UUID
    ) -> DataQualityIssue | None:
        statement = (
            select(DataQualityIssue)
            .where(
                DataQualityIssue.company_id == company_id,
                DataQualityIssue.id == issue_id,
            )
            .with_for_update()
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_hourly_activity_payloads(
        self,
        *,
        company_id: UUID,
        site_id: UUID,
        reporting_period_id: UUID,
        metric_definition_id: UUID,
    ) -> Sequence[dict[str, Any]]:
        statement = (
            select(RawActivityRecord.raw_payload)
            .join(
                ActivityRecord,
                and_(
                    ActivityRecord.company_id == RawActivityRecord.company_id,
                    ActivityRecord.raw_activity_record_id == RawActivityRecord.id,
                ),
            )
            .where(
                ActivityRecord.company_id == company_id,
                ActivityRecord.site_id == site_id,
                ActivityRecord.reporting_period_id == reporting_period_id,
                ActivityRecord.metric_definition_id == metric_definition_id,
                ActivityRecord.status == "valid",
                RawActivityRecord.import_status == "accepted",
            )
        )
        return (await self.session.execute(statement)).scalars().all()

    async def get_data_source(self, company_id: UUID, import_id: UUID) -> DataSource | None:
        statement = select(DataSource).where(
            DataSource.company_id == company_id,
            DataSource.id == import_id,
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_source_document_for_import(
        self, company_id: UUID, import_id: UUID
    ) -> SourceDocument | None:
        statement = (
            select(SourceDocument)
            .where(
                SourceDocument.company_id == company_id,
                SourceDocument.data_source_id == import_id,
            )
            .order_by(SourceDocument.version.desc())
            .limit(1)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_import_issues(
        self, company_id: UUID, import_id: UUID
    ) -> Sequence[DataQualityIssue]:
        statement = self._issues_for_import_query(company_id, import_id).order_by(
            DataQualityIssue.created_at, DataQualityIssue.id
        )
        return (await self.session.execute(statement)).scalars().all()

    async def list_issues(
        self,
        *,
        company_id: UUID,
        status: str | None,
        severity: str | None,
        code: str | None,
        issue_type: str | None,
        import_id: UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[DataQualityIssue], int]:
        filters = [DataQualityIssue.company_id == company_id]
        if status is not None:
            filters.append(DataQualityIssue.status == status)
        if severity is not None:
            filters.append(DataQualityIssue.severity == severity)
        if code is not None:
            filters.append(DataQualityIssue.code == code)
        if issue_type is not None:
            filters.append(DataQualityIssue.issue_type == issue_type)

        statement: Select[tuple[DataQualityIssue]] = select(DataQualityIssue)
        count_statement = select(func.count(DataQualityIssue.id))
        if import_id is not None:
            import_filter = or_(
                RawActivityRecord.data_source_id == import_id,
                DataQualityIssue.details["import_id"].astext == str(import_id),
            )
            join_condition = and_(
                RawActivityRecord.company_id == DataQualityIssue.company_id,
                RawActivityRecord.id == DataQualityIssue.raw_activity_record_id,
            )
            statement = statement.outerjoin(RawActivityRecord, join_condition)
            count_statement = count_statement.outerjoin(RawActivityRecord, join_condition)
            filters.append(import_filter)

        statement = (
            statement.where(*filters)
            .order_by(DataQualityIssue.created_at.desc(), DataQualityIssue.id.desc())
            .limit(limit)
            .offset(offset)
        )
        count_statement = count_statement.where(*filters)
        rows = (await self.session.execute(statement)).scalars().all()
        total = int((await self.session.execute(count_statement)).scalar_one())
        return rows, total

    @staticmethod
    def _issues_for_import_query(
        company_id: UUID, import_id: UUID
    ) -> Select[tuple[DataQualityIssue]]:
        join_condition = and_(
            RawActivityRecord.company_id == DataQualityIssue.company_id,
            RawActivityRecord.id == DataQualityIssue.raw_activity_record_id,
        )
        return (
            select(DataQualityIssue)
            .outerjoin(RawActivityRecord, join_condition)
            .where(
                DataQualityIssue.company_id == company_id,
                or_(
                    RawActivityRecord.data_source_id == import_id,
                    DataQualityIssue.details["import_id"].astext == str(import_id),
                ),
            )
        )
