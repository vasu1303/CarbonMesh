from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.core import (
    Actor,
    Company,
    DataSource,
    EvidenceItem,
    ReportingPeriod,
    Site,
    SourceDocument,
)


@dataclass(frozen=True, slots=True)
class StoredSourceUpload:
    source: DataSource
    document: SourceDocument
    evidence: list[EvidenceItem]


class SourceRepositoryProtocol(Protocol):
    async def get_company(self, *, company_id: UUID) -> Company | None: ...

    async def get_site(self, *, company_id: UUID, site_id: UUID) -> Site | None: ...

    async def get_reporting_period(
        self, *, company_id: UUID, reporting_period_id: UUID
    ) -> ReportingPeriod | None: ...

    async def get_actor(self, *, company_id: UUID, actor_id: UUID) -> Actor | None: ...

    async def acquire_company_upload_lock(self, *, company_id: UUID) -> None: ...

    async def get_upload_by_checksum(
        self, *, company_id: UUID, checksum: str
    ) -> StoredSourceUpload | None: ...

    async def get_source_by_name(self, *, company_id: UUID, name: str) -> DataSource | None: ...

    async def next_document_version(self, *, data_source_id: UUID) -> int: ...

    def add(self, instance: object) -> None: ...

    async def flush(self) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


def _company_lock_id(company_id: UUID) -> int:
    digest = hashlib.sha256(f"source-upload:{company_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_company(self, *, company_id: UUID) -> Company | None:
        return await self._session.scalar(
            select(Company).where(Company.id == company_id, Company.is_active.is_(True))
        )

    async def get_site(self, *, company_id: UUID, site_id: UUID) -> Site | None:
        return await self._session.scalar(
            select(Site).where(
                Site.company_id == company_id,
                Site.id == site_id,
                Site.is_active.is_(True),
            )
        )

    async def get_reporting_period(
        self,
        *,
        company_id: UUID,
        reporting_period_id: UUID,
    ) -> ReportingPeriod | None:
        return await self._session.scalar(
            select(ReportingPeriod).where(
                ReportingPeriod.company_id == company_id,
                ReportingPeriod.id == reporting_period_id,
            )
        )

    async def get_actor(self, *, company_id: UUID, actor_id: UUID) -> Actor | None:
        return await self._session.scalar(
            select(Actor).where(
                Actor.company_id == company_id,
                Actor.id == actor_id,
                Actor.is_active.is_(True),
            )
        )

    async def acquire_company_upload_lock(self, *, company_id: UUID) -> None:
        await self._session.execute(
            select(func.pg_advisory_xact_lock(_company_lock_id(company_id)))
        )

    async def get_upload_by_checksum(
        self,
        *,
        company_id: UUID,
        checksum: str,
    ) -> StoredSourceUpload | None:
        row = await self._session.execute(
            select(DataSource, SourceDocument)
            .join(
                SourceDocument,
                (SourceDocument.company_id == DataSource.company_id)
                & (SourceDocument.data_source_id == DataSource.id),
            )
            .where(
                SourceDocument.company_id == company_id,
                SourceDocument.checksum == checksum,
            )
            .limit(1)
        )
        source_document = row.one_or_none()
        if source_document is None:
            return None
        source, document = source_document
        evidence = list(
            await self._session.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.company_id == company_id,
                    EvidenceItem.source_document_id == document.id,
                )
                .order_by(EvidenceItem.locator.asc(), EvidenceItem.id.asc())
            )
        )
        return StoredSourceUpload(source=source, document=document, evidence=evidence)

    async def get_source_by_name(
        self,
        *,
        company_id: UUID,
        name: str,
    ) -> DataSource | None:
        return await self._session.scalar(
            select(DataSource).where(
                DataSource.company_id == company_id,
                DataSource.name == name,
            )
        )

    async def next_document_version(self, *, data_source_id: UUID) -> int:
        current = await self._session.scalar(
            select(func.max(SourceDocument.version)).where(
                SourceDocument.data_source_id == data_source_id
            )
        )
        return int(current or 0) + 1

    def add(self, instance: object) -> None:
        self._session.add(instance)

    async def flush(self) -> None:
        await self._session.flush()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
