from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.carbon import DataQualityIssue, RawActivityRecord
from app.db.models.core import DataSource, ReportingPeriod, SourceDocument
from app.db.models.procurement import Supplier, SupplierProduct
from app.db.models.semantic import MetricDefinition
from app.modules.imports.parser import ImportPayloadError, document_bytes, parse_import_content
from app.modules.imports.repository import ImportRepository
from app.modules.imports.schemas import (
    ActivityImportRequest,
    ActivityRow,
    DataQualityIssueList,
    DataQualityIssueRead,
    DataQualityIssueUpdate,
    HourlyElectricityRow,
    ImportResult,
    SupplierImportRequest,
    SupplierProductRow,
)

_SIX_PLACES = Decimal("0.000001")
_TWELVE_PLACES = Decimal("0.000000000001")
_FOUR_PLACES = Decimal("0.0001")
MAX_IMPORT_ROWS = 5_000
MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_INLINE_ISSUES = 100
_MASS_TO_KG = {
    "g": Decimal("0.001"),
    "gram": Decimal("0.001"),
    "grams": Decimal("0.001"),
    "mg": Decimal("0.000001"),
    "milligram": Decimal("0.000001"),
    "milligrams": Decimal("0.000001"),
    "kg": Decimal(1),
    "kilogram": Decimal(1),
    "kilograms": Decimal(1),
    "lb": Decimal("0.45359237"),
    "lbs": Decimal("0.45359237"),
    "pound": Decimal("0.45359237"),
    "pounds": Decimal("0.45359237"),
    "t": Decimal(1000),
    "tonne": Decimal(1000),
    "tonnes": Decimal(1000),
    "metric ton": Decimal(1000),
    "metric tons": Decimal(1000),
    "metric tonne": Decimal(1000),
    "metric tonnes": Decimal(1000),
}
_ENERGY_TO_KWH = {"wh": Decimal("0.001"), "kwh": Decimal(1), "mwh": Decimal(1000)}


class ImportServiceError(RuntimeError):
    code = "import_error"

    def __init__(self, message: str, *, field_name: str | None = None) -> None:
        super().__init__(message)
        self.field_name = field_name


class ImportReferenceNotFound(ImportServiceError):
    code = "reference_not_found"


class InvalidImportContext(ImportServiceError):
    code = "invalid_import_context"


class ImportRunNotFound(ImportServiceError):
    code = "import_not_found"


class ImportIdempotencyConflict(ImportServiceError):
    code = "import_idempotency_conflict"


class QualityIssueNotFound(ImportServiceError):
    code = "quality_issue_not_found"


class QualityIssueConflict(ImportServiceError):
    code = "quality_issue_conflict"


class QualityIssueForbidden(ImportServiceError):
    code = "quality_issue_forbidden"


class UnsupportedUnitConversion(ValueError):
    """The source and canonical units do not have an allowlisted conversion."""


class NormalizedQuantityOutOfRange(ValueError):
    """The converted value cannot fit carbon.activity_records Numeric(24,6)."""


class ImportService:
    """Transaction owner for deterministic activity and supplier imports."""

    def __init__(
        self,
        session: AsyncSession,
        repository: ImportRepository | None = None,
        *,
        actor_id: UUID | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or ImportRepository(session)
        self.actor_id = actor_id
        self.trace_id = trace_id

    async def import_activity(self, request: ActivityImportRequest) -> ImportResult:
        try:
            replay = await self._replay_import(request, "activity")
            if replay is not None:
                return replay
            return await self._import_activity(request)
        except Exception:
            await self.session.rollback()
            raise

    async def _import_activity(self, request: ActivityImportRequest) -> ImportResult:
        period, metric = await self._validate_activity_context(request)
        hourly = metric.key == "activity.electricity_consumption"
        payload_bytes = document_bytes(request.content)
        checksum = _sha256(payload_bytes)
        duplicate_document = await self.repository.find_document_by_checksum(
            request.company_id, checksum
        )
        source = None
        document = None
        if duplicate_document is not None and hourly:
            source = await self._claim_pending_hourly_document(request, duplicate_document)
            if source is not None:
                document = duplicate_document
                duplicate_document = None
        if source is None:
            source = await self._create_source(
                company_id=request.company_id,
                site_id=request.site_id,
                source_name=request.source_name,
                source_type=_source_type(request.content_type),
                external_reference=request.external_reference,
                import_type="activity",
                filename=request.filename,
                checksum=checksum,
                is_synthetic=request.is_synthetic,
                idempotency_key=request.idempotency_key,
                request_hash=_import_request_hash(request),
            )
        issues: list[DataQualityIssue] = []
        source.configuration = {
            **source.configuration,
            "activity_kind": "hourly_electricity" if hourly else "purchased_material",
            "site_id": str(request.site_id),
            "reporting_period_id": str(request.reporting_period_id),
            "metric_definition_id": str(request.metric_definition_id),
        }

        if duplicate_document is not None:
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code="duplicate_document",
                    issue_type="duplicate",
                    message="This document checksum has already been imported.",
                    details={"existing_source_document_id": str(duplicate_document.id)},
                )
            )
            return await self._finish_import(
                source=source,
                document=None,
                accepted_count=0,
                rejected_count=1,
                issues=issues,
            )

        if document is None:
            document = await self._create_document(
                source=source,
                filename=request.filename,
                content_type=request.content_type,
                checksum=checksum,
                payload_bytes=payload_bytes,
                import_type="activity",
            )
        if request.checksum is not None and request.checksum != checksum:
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code="document_checksum_mismatch",
                    issue_type="checksum",
                    message="The supplied document checksum does not match its content.",
                    details={"expected": request.checksum, "actual": checksum},
                )
            )
            return await self._finish_import(
                source=source,
                document=document,
                accepted_count=0,
                rejected_count=1,
                issues=issues,
            )
        if len(payload_bytes) > MAX_IMPORT_BYTES:
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code="payload_limit_exceeded",
                    issue_type="validation",
                    message=f"Import documents are limited to {MAX_IMPORT_BYTES} bytes.",
                    details={
                        "maximum_bytes": MAX_IMPORT_BYTES,
                        "actual_bytes": len(payload_bytes),
                    },
                )
            )
            return await self._finish_import(
                source=source,
                document=document,
                accepted_count=0,
                rejected_count=1,
                issues=issues,
            )

        try:
            parsed = parse_import_content(request.content, request.content_type)
        except ImportPayloadError as error:
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code="invalid_document",
                    issue_type="validation",
                    message=str(error),
                )
            )
            return await self._finish_import(
                source=source,
                document=document,
                accepted_count=0,
                rejected_count=1,
                issues=issues,
            )

        if not parsed.records:
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code="empty_document",
                    issue_type="validation",
                    message="The activity document contains no data rows.",
                )
            )
            return await self._finish_import(
                source=source,
                document=document,
                accepted_count=0,
                rejected_count=0,
                issues=issues,
            )
        if len(parsed.records) > MAX_IMPORT_ROWS:
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code="row_limit_exceeded",
                    issue_type="validation",
                    message=f"Activity imports are limited to {MAX_IMPORT_ROWS} rows.",
                    details={
                        "maximum_rows": MAX_IMPORT_ROWS,
                        "actual_rows": len(parsed.records),
                    },
                )
            )
            return await self._finish_import(
                source=source,
                document=document,
                accepted_count=0,
                rejected_count=len(parsed.records),
                issues=issues,
            )

        hourly_rows: dict[int, HourlyElectricityRow] = {}
        existing_timestamps: set[datetime] = set()
        if hourly:
            for index, raw_row in enumerate(parsed.records):
                try:
                    hourly_rows[index] = HourlyElectricityRow.model_validate(raw_row)
                except ValidationError:
                    pass  # Persist the raw row and its typed validation errors below.
            existing = await self.repository.list_hourly_activity_payloads(
                company_id=request.company_id,
                site_id=request.site_id,
                reporting_period_id=request.reporting_period_id,
                metric_definition_id=request.metric_definition_id,
            )
            for payload in existing:
                try:
                    existing_timestamps.add(HourlyElectricityRow.model_validate(payload).timestamp)
                except ValidationError as error:
                    raise InvalidImportContext(
                        "Existing hourly activity has an invalid timestamp or quantity."
                    ) from error
        timestamp_counts = Counter(row.timestamp for row in hourly_rows.values())
        accepted_timestamps = set(existing_timestamps)
        accepted_count = 0
        rejected_count = 0
        seen_row_keys: set[str] = set()
        persisted_row_keys: set[str] = set()
        for row_number, raw_row in zip(parsed.row_numbers, parsed.records, strict=True):
            row_checksum = _row_checksum(raw_row)
            requested_row_key = str(raw_row.get("row_key") or row_checksum).strip()
            safe_row_key = _safe_row_key(requested_row_key, row_checksum, row_number)
            is_duplicate = requested_row_key in seen_row_keys
            if is_duplicate:
                safe_row_key = _safe_row_key(
                    f"duplicate:{requested_row_key}:{row_number}", row_checksum, row_number
                )
            seen_row_keys.add(requested_row_key)
            safe_row_key = _unique_persisted_row_key(
                safe_row_key,
                checksum=row_checksum,
                row_number=row_number,
                persisted=persisted_row_keys,
            )
            persisted_row_keys.add(safe_row_key)

            raw_record = await self.repository.create_raw_activity(
                company_id=request.company_id,
                data_source_id=source.id,
                source_document_id=document.id,
                row_key=safe_row_key,
                row_number=row_number,
                raw_payload=_json_safe_mapping(raw_row),
                checksum=row_checksum,
            )

            if is_duplicate:
                issues.append(
                    await self._create_row_issue(
                        source=source,
                        raw_record=raw_record,
                        code="duplicate_row",
                        issue_type="duplicate",
                        message="The row key is duplicated within this import.",
                        field_name="row_key",
                        details={"row_key": requested_row_key},
                    )
                )
                raw_record.import_status = "rejected"
                rejected_count += 1
                continue

            try:
                row = (
                    HourlyElectricityRow.model_validate(raw_row)
                    if hourly
                    else ActivityRow.model_validate(raw_row)
                )
            except ValidationError as error:
                issues.extend(
                    await self._create_validation_issues(
                        source=source,
                        raw_record=raw_record,
                        error=error,
                    )
                )
                raw_record.import_status = "rejected"
                rejected_count += 1
                continue

            if isinstance(row, HourlyElectricityRow) and (
                timestamp_counts[row.timestamp] > 1 or row.timestamp in existing_timestamps
            ):
                issues.append(
                    await self._create_row_issue(
                        source=source,
                        raw_record=raw_record,
                        code="duplicate_timestamp",
                        issue_type="duplicate",
                        field_name="timestamp",
                        message="The UTC hourly interval is duplicated in the selected context.",
                        details={"timestamp": row.timestamp.isoformat()},
                    )
                )
                raw_record.import_status = "rejected"
                rejected_count += 1
                continue

            row_errors, product = await self._validate_activity_row(
                source=source,
                raw_record=raw_record,
                row=row,
                request=request,
                period=period,
                metric=metric,
            )
            if row_errors:
                issues.extend(row_errors)
                raw_record.import_status = "rejected"
                rejected_count += 1
                continue

            normalized_quantity = normalize_quantity(row.quantity, row.unit, metric.canonical_unit)
            activity = await self.repository.create_activity(
                company_id=request.company_id,
                raw_activity_record_id=raw_record.id,
                site_id=request.site_id,
                reporting_period_id=request.reporting_period_id,
                metric_definition_id=request.metric_definition_id,
                supplier_product_id=product.id if product is not None else None,
                material_code=row.material_code,
                activity_date=row.activity_date,
                quantity=row.quantity.quantize(_SIX_PLACES, rounding=ROUND_HALF_EVEN),
                unit=row.unit,
                normalized_quantity=normalized_quantity,
                normalized_unit=metric.canonical_unit,
                unit_cost=(
                    row.unit_cost.quantize(_SIX_PLACES, rounding=ROUND_HALF_EVEN)
                    if row.unit_cost is not None
                    else None
                ),
                currency=row.currency,
                status="valid",
            )
            if product is not None and product.material_code != activity.material_code:
                # Defensive invariant; the same check occurs before persistence.
                raise RuntimeError("Validated supplier product material changed during import.")
            raw_record.import_status = "accepted"
            accepted_count += 1
            if isinstance(row, HourlyElectricityRow):
                accepted_timestamps.add(row.timestamp)

        if hourly:
            observed = [
                row.timestamp
                for row in hourly_rows.values()
                if period.start_date <= row.timestamp.date() <= period.end_date
            ]
            interval_start = request.interval_start or (min(observed) if observed else None)
            interval_end = request.interval_end or (
                max(observed) + timedelta(hours=1) if observed else None
            )
            if interval_start is not None and interval_end is not None:
                source.configuration = {
                    **source.configuration,
                    "interval_start": interval_start.isoformat(),
                    "interval_end": interval_end.isoformat(),
                    "expected_intervals": int(
                        (interval_end - interval_start).total_seconds() // 3600
                    ),
                }
                next_expected = interval_start
                gaps: list[tuple[datetime, datetime]] = []
                for timestamp in sorted(
                    value for value in accepted_timestamps if interval_start <= value < interval_end
                ):
                    if timestamp > next_expected:
                        gaps.append((next_expected, timestamp))
                    next_expected = timestamp + timedelta(hours=1)
                if next_expected < interval_end:
                    gaps.append((next_expected, interval_end))
                for gap_start, gap_end in gaps:
                    issues.append(
                        await self._create_general_issue(
                            source=source,
                            code="missing_interval",
                            issue_type="completeness",
                            field_name="timestamp",
                            message="The hourly activity interval is incomplete; no values were inferred.",
                            details={
                                "interval_start": gap_start.isoformat(),
                                "interval_end": gap_end.isoformat(),
                                "missing_count": int((gap_end - gap_start).total_seconds() // 3600),
                            },
                        )
                    )

        return await self._finish_import(
            source=source,
            document=document,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            issues=issues,
        )

    async def import_suppliers(self, request: SupplierImportRequest) -> ImportResult:
        try:
            replay = await self._replay_import(request, "suppliers")
            if replay is not None:
                return replay
            return await self._import_suppliers(request)
        except Exception:
            await self.session.rollback()
            raise

    async def _import_suppliers(self, request: SupplierImportRequest) -> ImportResult:
        if not await self.repository.company_exists(request.company_id):
            raise ImportReferenceNotFound("The company does not exist.", field_name="company_id")

        payload_bytes = document_bytes(request.content)
        checksum = _sha256(payload_bytes)
        source = await self._create_source(
            company_id=request.company_id,
            site_id=None,
            source_name=request.source_name,
            source_type=_source_type(request.content_type),
            external_reference=request.external_reference,
            import_type="suppliers",
            filename=request.filename,
            checksum=checksum,
            is_synthetic=request.is_synthetic,
            idempotency_key=request.idempotency_key,
            request_hash=_import_request_hash(request),
        )
        issues: list[DataQualityIssue] = []

        duplicate_document = await self.repository.find_document_by_checksum(
            request.company_id, checksum
        )
        if duplicate_document is not None:
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code="duplicate_document",
                    issue_type="duplicate",
                    message="This document checksum has already been imported.",
                    details={"existing_source_document_id": str(duplicate_document.id)},
                )
            )
            return await self._finish_import(
                source=source,
                document=None,
                accepted_count=0,
                rejected_count=1,
                issues=issues,
            )

        document = await self._create_document(
            source=source,
            filename=request.filename,
            content_type=request.content_type,
            checksum=checksum,
            payload_bytes=payload_bytes,
            import_type="suppliers",
        )
        if request.checksum is not None and request.checksum != checksum:
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code="document_checksum_mismatch",
                    issue_type="checksum",
                    message="The supplied document checksum does not match its content.",
                    details={"expected": request.checksum, "actual": checksum},
                )
            )
            return await self._finish_import(
                source=source,
                document=document,
                accepted_count=0,
                rejected_count=1,
                issues=issues,
            )
        if len(payload_bytes) > MAX_IMPORT_BYTES:
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code="payload_limit_exceeded",
                    issue_type="validation",
                    message=f"Import documents are limited to {MAX_IMPORT_BYTES} bytes.",
                    details={
                        "maximum_bytes": MAX_IMPORT_BYTES,
                        "actual_bytes": len(payload_bytes),
                    },
                )
            )
            return await self._finish_import(
                source=source,
                document=document,
                accepted_count=0,
                rejected_count=1,
                issues=issues,
            )

        try:
            parsed = parse_import_content(request.content, request.content_type)
        except ImportPayloadError as error:
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code="invalid_document",
                    issue_type="validation",
                    message=str(error),
                )
            )
            return await self._finish_import(
                source=source,
                document=document,
                accepted_count=0,
                rejected_count=1,
                issues=issues,
            )

        if not parsed.records:
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code="empty_document",
                    issue_type="validation",
                    message="The supplier document contains no data rows.",
                )
            )
            return await self._finish_import(
                source=source,
                document=document,
                accepted_count=0,
                rejected_count=0,
                issues=issues,
            )
        if len(parsed.records) > MAX_IMPORT_ROWS:
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code="row_limit_exceeded",
                    issue_type="validation",
                    message=f"Supplier imports are limited to {MAX_IMPORT_ROWS} rows.",
                    details={
                        "maximum_rows": MAX_IMPORT_ROWS,
                        "actual_rows": len(parsed.records),
                    },
                )
            )
            return await self._finish_import(
                source=source,
                document=document,
                accepted_count=0,
                rejected_count=len(parsed.records),
                issues=issues,
            )

        accepted_count = 0
        rejected_count = 0
        seen_products: set[tuple[str, str]] = set()
        seen_locators: set[str] = set()
        supplier_cache: dict[str, Supplier] = {}
        for row_number, raw_row in zip(parsed.row_numbers, parsed.records, strict=True):
            try:
                row = SupplierProductRow.model_validate(raw_row)
            except ValidationError as error:
                issues.extend(
                    await self._create_supplier_validation_issues(
                        source=source,
                        row_number=row_number,
                        error=error,
                    )
                )
                rejected_count += 1
                continue

            product_key = (row.supplier_code, row.product_code)
            if product_key in seen_products:
                issues.append(
                    await self._create_general_issue(
                        source=source,
                        code="duplicate_product",
                        issue_type="duplicate",
                        message="The supplier product is duplicated within this import.",
                        field_name="product_code",
                        details={
                            "row_number": row_number,
                            "supplier_code": row.supplier_code,
                            "product_code": row.product_code,
                        },
                    )
                )
                rejected_count += 1
                continue
            seen_products.add(product_key)

            locator = row.evidence_locator or f"row:{row_number}"
            if locator in seen_locators:
                issues.append(
                    await self._create_general_issue(
                        source=source,
                        code="duplicate_evidence_locator",
                        issue_type="duplicate",
                        message="Evidence locators must be unique within a document.",
                        field_name="evidence_locator",
                        details={"row_number": row_number, "locator": locator},
                    )
                )
                rejected_count += 1
                continue

            evidence_checksum = _sha256(row.evidence_text.encode("utf-8"))
            if row.evidence_checksum is not None and row.evidence_checksum != evidence_checksum:
                issues.append(
                    await self._create_general_issue(
                        source=source,
                        code="evidence_checksum_mismatch",
                        issue_type="checksum",
                        message="The evidence checksum does not match the evidence text.",
                        field_name="evidence_checksum",
                        details={
                            "row_number": row_number,
                            "expected": row.evidence_checksum,
                            "actual": evidence_checksum,
                        },
                    )
                )
                rejected_count += 1
                continue
            if row.pcf_unit != "kgCO2e/kg":
                issues.append(
                    await self._create_general_issue(
                        source=source,
                        code="unsupported_pcf_unit",
                        issue_type="validation",
                        message="Supplier PCF must use the canonical kgCO2e/kg unit.",
                        field_name="pcf_unit",
                        details={"row_number": row_number, "unit": row.pcf_unit},
                    )
                )
                rejected_count += 1
                continue

            supplier = supplier_cache.get(row.supplier_code)
            if supplier is None:
                supplier = await self.repository.get_supplier(request.company_id, row.supplier_code)
                if supplier is None:
                    supplier = await self.repository.create_supplier(
                        company_id=request.company_id,
                        supplier_code=row.supplier_code,
                        name=row.supplier_name,
                        country_code=row.supplier_country_code,
                        metadata=_json_safe_mapping(row.supplier_metadata),
                    )
                supplier_cache[row.supplier_code] = supplier
            if (
                supplier.name != row.supplier_name
                or supplier.country_code != row.supplier_country_code
            ):
                issues.append(
                    await self._create_general_issue(
                        source=source,
                        code="supplier_reference_conflict",
                        issue_type="reference",
                        message="Supplier code resolves to a different name or country.",
                        field_name="supplier_code",
                        details={"row_number": row_number},
                    )
                )
                rejected_count += 1
                continue
            if supplier.status != "active":
                issues.append(
                    await self._create_general_issue(
                        source=source,
                        code="supplier_inactive",
                        issue_type="reference",
                        message="Supplier products cannot be imported for an inactive supplier.",
                        field_name="supplier_code",
                        details={"row_number": row_number},
                    )
                )
                rejected_count += 1
                continue

            existing_product = await self.repository.get_supplier_product_for_supplier(
                request.company_id, supplier.id, row.product_code
            )
            if existing_product is not None:
                issues.append(
                    await self._create_general_issue(
                        source=source,
                        code="duplicate_product",
                        issue_type="duplicate",
                        message="The supplier product already exists.",
                        field_name="product_code",
                        details={
                            "row_number": row_number,
                            "existing_supplier_product_id": str(existing_product.id),
                        },
                    )
                )
                rejected_count += 1
                continue

            evidence = await self.repository.create_evidence_item(
                company_id=request.company_id,
                source_document_id=document.id,
                evidence_type=row.evidence_type,
                locator=locator,
                content_text=row.evidence_text,
                checksum=evidence_checksum,
                metadata={
                    **_json_safe_mapping(row.evidence_metadata),
                    "import_id": str(source.id),
                    "row_number": row_number,
                    "is_synthetic": request.is_synthetic,
                    "trust_status": "synthetic" if request.is_synthetic else "accepted",
                    "source_version": document.version,
                    "source_checksum": document.checksum,
                    "embedding_status": "pending_explicit_index",
                },
            )
            seen_locators.add(locator)
            await self.repository.create_supplier_product(
                company_id=request.company_id,
                supplier_id=supplier.id,
                evidence_item_id=evidence.id,
                product_code=row.product_code,
                name=row.product_name,
                material_code=row.material_code,
                category=row.category,
                description=row.description,
                pcf_kgco2e_per_unit=row.pcf_kgco2e_per_unit.quantize(
                    _TWELVE_PLACES, rounding=ROUND_HALF_EVEN
                ),
                pcf_unit=row.pcf_unit,
                circularity_score=row.circularity_score.quantize(
                    _FOUR_PLACES, rounding=ROUND_HALF_EVEN
                ),
                recycled_content_pct=row.recycled_content_pct.quantize(
                    _FOUR_PLACES, rounding=ROUND_HALF_EVEN
                ),
                recyclable_pct=row.recyclable_pct.quantize(_FOUR_PLACES, rounding=ROUND_HALF_EVEN),
                evidence_quality_score=row.evidence_quality_score.quantize(
                    _FOUR_PLACES, rounding=ROUND_HALF_EVEN
                ),
                lead_time_days=row.lead_time_days,
                unit_cost=row.unit_cost.quantize(_SIX_PLACES, rounding=ROUND_HALF_EVEN),
                currency=row.currency,
                effective_from=row.effective_from,
                effective_to=row.effective_to,
                is_active=row.is_active,
            )
            accepted_count += 1

        return await self._finish_import(
            source=source,
            document=document,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            issues=issues,
        )

    async def _claim_pending_hourly_document(
        self, request: ActivityImportRequest, document: SourceDocument
    ) -> DataSource | None:
        """Consume a declared, unprocessed synthetic fixture once under the document lock."""
        if (
            not request.is_synthetic
            or document.content_type != request.content_type
            or (request.checksum is not None and request.checksum != document.checksum)
        ):
            return None
        source = await self.repository.get_data_source(request.company_id, document.data_source_id)
        if source is None or not source.is_synthetic or source.site_id != request.site_id:
            return None
        configuration = source.configuration
        if not (
            configuration.get("import_type") == "hourly_electricity"
            and configuration.get("import_status") == "pending"
            and configuration.get("reporting_period_id") == str(request.reporting_period_id)
            and configuration.get("metric_definition_id") == str(request.metric_definition_id)
        ):
            return None
        if await self.repository.has_raw_rows_for_document(request.company_id, document.id):
            return None
        source.configuration = {
            **configuration,
            "import_type": "activity",
            "import_status": "processing",
            "source_name": request.source_name,
            "filename": request.filename,
            "document_checksum": document.checksum,
            "accepted_count": 0,
            "rejected_count": 0,
            "issue_count": 0,
            "idempotency_key": request.idempotency_key,
            "request_hash": _import_request_hash(request),
        }
        return source

    async def _replay_import(
        self, request: ActivityImportRequest | SupplierImportRequest, import_type: str
    ) -> ImportResult | None:
        if request.idempotency_key is None:
            return None
        lock_bytes = hashlib.sha256(
            f"{request.company_id}:{import_type}:{request.idempotency_key}".encode()
        ).digest()[:8]
        await self.repository.lock_import_key(int.from_bytes(lock_bytes, "big", signed=True))
        existing = await self.repository.find_import_by_key(
            request.company_id, import_type, request.idempotency_key
        )
        if existing is None:
            return None
        if existing.configuration.get("request_hash") != _import_request_hash(request):
            raise ImportIdempotencyConflict(
                "The idempotency key is already bound to a different import payload."
            )
        result = await self.get_import(company_id=request.company_id, import_id=existing.id)
        await self.session.commit()
        return result

    async def get_import(self, *, company_id: UUID, import_id: UUID) -> ImportResult:
        source = await self.repository.get_data_source(company_id, import_id)
        if source is None or source.configuration.get("import_type") not in {
            "activity",
            "suppliers",
        }:
            raise ImportRunNotFound("The requested import does not exist.")
        document = await self.repository.get_source_document_for_import(company_id, import_id)
        issues = list(await self.repository.list_import_issues(company_id, import_id))
        return _build_import_result(source, document, issues)

    async def list_data_quality_issues(
        self,
        *,
        company_id: UUID,
        status: str | None = "open",
        severity: str | None = None,
        code: str | None = None,
        issue_type: str | None = None,
        import_id: UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> DataQualityIssueList:
        if not await self.repository.company_exists(company_id):
            raise ImportReferenceNotFound("The company does not exist.", field_name="company_id")
        rows, total = await self.repository.list_issues(
            company_id=company_id,
            status=status,
            severity=severity,
            code=code,
            issue_type=issue_type,
            import_id=import_id,
            limit=limit,
            offset=offset,
        )
        return DataQualityIssueList(
            items=[_to_issue_read(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def update_data_quality_issue(
        self,
        issue_id: UUID,
        request: DataQualityIssueUpdate,
        *,
        trace_id: str | None = None,
    ) -> DataQualityIssueRead:
        """Record a human review; rejected input never becomes valid by review alone."""
        try:
            issue = await self.repository.get_issue_for_update(request.company_id, issue_id)
            if issue is None:
                raise QualityIssueNotFound("The requested data-quality issue does not exist.")
            actor = await self.repository.get_actor(request.company_id, request.actor_id)
            if actor is None or actor.role not in {"sustainability_analyst", "approver"}:
                raise QualityIssueForbidden(
                    "An active sustainability analyst or approver must review this issue."
                )
            decision = {
                "status": request.status,
                "actor_id": str(request.actor_id),
                "decision_note": request.decision_note,
            }
            previous_decision = issue.details.get("review")
            if issue.status != "open":
                if previous_decision == decision:
                    result = _to_issue_read(issue)
                    await self.session.commit()
                    return result
                raise QualityIssueConflict("The issue already has a different review decision.")
            if request.status == "waived" and issue.severity == "error":
                raise QualityIssueConflict(
                    "Blocking errors cannot be waived; correct the source data and resolve the issue."
                )
            previous_status = issue.status
            issue.status = request.status
            issue.updated_at = datetime.now(UTC)
            issue.details = {
                **issue.details,
                "review": decision,
                "reviewed_at": issue.updated_at.isoformat(),
                "data_validity_unchanged": True,
            }
            await self.repository.create_audit_log(
                company_id=request.company_id,
                actor_id=request.actor_id,
                action=f"data_quality.{request.status}",
                entity_type="data_quality_issue",
                entity_id=issue.id,
                trace_id=trace_id or f"quality:{issue.id}",
                details={"previous_status": previous_status, **decision},
            )
            await self.session.flush()
            result = _to_issue_read(issue)
            await self.session.commit()
            return result
        except Exception:
            await self.session.rollback()
            raise

    async def _validate_activity_context(
        self, request: ActivityImportRequest
    ) -> tuple[ReportingPeriod, MetricDefinition]:
        if not await self.repository.company_exists(request.company_id):
            raise ImportReferenceNotFound("The company does not exist.", field_name="company_id")
        site = await self.repository.get_site(request.company_id, request.site_id)
        if site is None or not site.is_active:
            raise ImportReferenceNotFound(
                "The active site does not exist for this company.", field_name="site_id"
            )
        period = await self.repository.get_reporting_period(
            request.company_id, request.reporting_period_id
        )
        if period is None:
            raise ImportReferenceNotFound(
                "The reporting period does not exist for this company.",
                field_name="reporting_period_id",
            )
        metric = await self.repository.get_metric_definition(
            request.company_id, request.metric_definition_id
        )
        if metric is None:
            raise ImportReferenceNotFound(
                "The active metric definition does not exist for this company.",
                field_name="metric_definition_id",
            )
        is_material = metric.key == "activity.purchased_material_mass" and _normalize_unit(
            metric.canonical_unit
        ) in {"kg", "kilogram", "kilograms"}
        is_electricity = (
            metric.key == "activity.electricity_consumption"
            and _normalize_unit(metric.canonical_unit) == "kwh"
        )
        if not (is_material or is_electricity):
            raise InvalidImportContext(
                "Activity imports require the purchased-material mass metric in canonical kg "
                "or the hourly electricity consumption metric in canonical kWh.",
                field_name="metric_definition_id",
            )
        if request.interval_start is not None:
            if not is_electricity:
                raise InvalidImportContext(
                    "Hourly interval bounds are only supported for electricity activity."
                )
            assert request.interval_end is not None
            if not (
                period.start_date <= request.interval_start.date()
                and (request.interval_end - timedelta(microseconds=1)).date() <= period.end_date
            ):
                raise InvalidImportContext("The hourly interval is outside the reporting period.")
        return period, metric

    async def _validate_activity_row(
        self,
        *,
        source: DataSource,
        raw_record: RawActivityRecord,
        row: ActivityRow,
        request: ActivityImportRequest,
        period: ReportingPeriod,
        metric: MetricDefinition,
    ) -> tuple[list[DataQualityIssue], SupplierProduct | None]:
        issues: list[DataQualityIssue] = []
        context_fields = {
            "site_id": (row.site_id, request.site_id),
            "reporting_period_id": (
                row.reporting_period_id,
                request.reporting_period_id,
            ),
            "metric_definition_id": (
                row.metric_definition_id,
                request.metric_definition_id,
            ),
        }
        for field_name, (row_value, frozen_value) in context_fields.items():
            if row_value is not None and row_value != frozen_value:
                issues.append(
                    await self._create_row_issue(
                        source=source,
                        raw_record=raw_record,
                        code="context_mismatch",
                        issue_type="reference",
                        message=f"Row {field_name} does not match the frozen import context.",
                        field_name=field_name,
                    )
                )

        if row.activity_date is not None and not (
            period.start_date <= row.activity_date <= period.end_date
        ):
            issues.append(
                await self._create_row_issue(
                    source=source,
                    raw_record=raw_record,
                    code="activity_date_outside_period",
                    issue_type="validation",
                    message="Activity date is outside the selected reporting period.",
                    field_name="activity_date",
                )
            )

        try:
            normalize_quantity(row.quantity, row.unit, metric.canonical_unit)
        except UnsupportedUnitConversion:
            issues.append(
                await self._create_row_issue(
                    source=source,
                    raw_record=raw_record,
                    code="unsupported_unit",
                    issue_type="validation",
                    message=(
                        f"Unit {row.unit!r} cannot be normalized to {metric.canonical_unit!r}."
                    ),
                    field_name="unit",
                )
            )
        except NormalizedQuantityOutOfRange:
            issues.append(
                await self._create_row_issue(
                    source=source,
                    raw_record=raw_record,
                    code="normalized_quantity_out_of_range",
                    issue_type="validation",
                    message="Normalized quantity exceeds the supported Numeric(24,6) range.",
                    field_name="quantity",
                )
            )

        if isinstance(row, HourlyElectricityRow):
            if (
                request.interval_start is not None
                and request.interval_end is not None
                and not (request.interval_start <= row.timestamp < request.interval_end)
            ):
                issues.append(
                    await self._create_row_issue(
                        source=source,
                        raw_record=raw_record,
                        code="timestamp_outside_interval",
                        issue_type="validation",
                        message="The timestamp is outside the declared hourly import interval.",
                        field_name="timestamp",
                    )
                )
            if row.site_code is not None:
                site = await self.repository.get_site(request.company_id, request.site_id)
                if site is None or row.site_code != site.code:
                    issues.append(
                        await self._create_row_issue(
                            source=source,
                            raw_record=raw_record,
                            code="context_mismatch",
                            issue_type="reference",
                            message="Row site_code does not match the frozen import context.",
                            field_name="site_code",
                        )
                    )
            return issues, None

        product = await self._resolve_activity_product(request.company_id, row)
        has_product_reference = row.supplier_product_id is not None or row.supplier_code is not None
        if not has_product_reference:
            issues.append(
                await self._create_row_issue(
                    source=source,
                    raw_record=raw_record,
                    code="supplier_product_required",
                    issue_type="reference",
                    message="A supplier product reference is required for purchased material.",
                    field_name="supplier_product_id",
                )
            )
        elif product is None:
            issues.append(
                await self._create_row_issue(
                    source=source,
                    raw_record=raw_record,
                    code="supplier_product_not_found",
                    issue_type="reference",
                    message="The supplier product reference does not exist for this company.",
                    field_name="supplier_product_id",
                )
            )
        elif product is not None and product.material_code != row.material_code:
            issues.append(
                await self._create_row_issue(
                    source=source,
                    raw_record=raw_record,
                    code="supplier_product_material_mismatch",
                    issue_type="reference",
                    message="The supplier product is not compatible with the row material.",
                    field_name="material_code",
                )
            )
        return issues, product

    async def _resolve_activity_product(
        self, company_id: UUID, row: ActivityRow
    ) -> SupplierProduct | None:
        product: SupplierProduct | None = None
        if row.supplier_product_id is not None:
            product = await self.repository.get_supplier_product(
                company_id, row.supplier_product_id
            )
        elif row.supplier_code is not None and row.supplier_product_code is not None:
            product = await self.repository.get_supplier_product_by_codes(
                company_id, row.supplier_code, row.supplier_product_code
            )
        if product is not None and not product.is_active:
            return None
        return product

    async def _create_source(
        self,
        *,
        company_id: UUID,
        site_id: UUID | None,
        source_name: str,
        source_type: str,
        external_reference: str | None,
        import_type: str,
        filename: str,
        checksum: str,
        is_synthetic: bool,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> DataSource:
        unique_name = f"{source_name[:140]} [{uuid4().hex[:12]}]"
        return await self.repository.create_data_source(
            company_id=company_id,
            site_id=site_id,
            name=unique_name,
            source_type=source_type,
            external_reference=external_reference,
            configuration={
                "import_type": import_type,
                "import_status": "processing",
                "source_name": source_name,
                "filename": filename,
                "document_checksum": checksum,
                "accepted_count": 0,
                "rejected_count": 0,
                "issue_count": 0,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
            },
            is_synthetic=is_synthetic,
        )

    async def _create_document(
        self,
        *,
        source: DataSource,
        filename: str,
        content_type: str,
        checksum: str,
        payload_bytes: bytes,
        import_type: str,
    ) -> SourceDocument:
        return await self.repository.create_source_document(
            company_id=source.company_id,
            data_source_id=source.id,
            filename=filename,
            content_type=content_type,
            checksum=checksum,
            size_bytes=len(payload_bytes),
            metadata={
                "import_id": str(source.id),
                "import_type": import_type,
                "is_synthetic": source.is_synthetic,
            },
        )

    async def _create_general_issue(
        self,
        *,
        source: DataSource,
        code: str,
        issue_type: str,
        message: str,
        field_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> DataQualityIssue:
        issue_details = {
            "import_id": str(source.id),
            **_hourly_issue_context(source),
            **(details or {}),
        }
        return await self.repository.create_issue(
            company_id=source.company_id,
            issue_type=issue_type,
            code=code,
            severity="error",
            message=message,
            field_name=field_name,
            details=issue_details,
        )

    async def _create_row_issue(
        self,
        *,
        source: DataSource,
        raw_record: RawActivityRecord,
        code: str,
        issue_type: str,
        message: str,
        field_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> DataQualityIssue:
        return await self.repository.create_issue(
            company_id=source.company_id,
            raw_activity_record_id=raw_record.id,
            issue_type=issue_type,
            code=code,
            severity="error",
            message=message,
            field_name=field_name,
            details={
                "import_id": str(source.id),
                "row_number": raw_record.row_number,
                **_hourly_issue_context(source),
                **(details or {}),
            },
        )

    async def _create_validation_issues(
        self,
        *,
        source: DataSource,
        raw_record: RawActivityRecord,
        error: ValidationError,
    ) -> list[DataQualityIssue]:
        issues: list[DataQualityIssue] = []
        for item in error.errors(include_input=False, include_context=False):
            field_name = _validation_field(item.get("loc", ()))
            code = (
                "missing_required_field" if item.get("type") == "missing" else "invalid_field_value"
            )
            issues.append(
                await self._create_row_issue(
                    source=source,
                    raw_record=raw_record,
                    code=code,
                    issue_type="validation",
                    message=str(item.get("msg", "Invalid field value.")),
                    field_name=field_name,
                    details={"validation_type": str(item.get("type", "value_error"))},
                )
            )
        return issues

    async def _create_supplier_validation_issues(
        self,
        *,
        source: DataSource,
        row_number: int,
        error: ValidationError,
    ) -> list[DataQualityIssue]:
        issues: list[DataQualityIssue] = []
        for item in error.errors(include_input=False, include_context=False):
            field_name = _validation_field(item.get("loc", ()))
            code = (
                "missing_required_field" if item.get("type") == "missing" else "invalid_field_value"
            )
            issues.append(
                await self._create_general_issue(
                    source=source,
                    code=code,
                    issue_type="validation",
                    message=str(item.get("msg", "Invalid field value.")),
                    field_name=field_name,
                    details={
                        "row_number": row_number,
                        "validation_type": str(item.get("type", "value_error")),
                    },
                )
            )
        return issues

    async def _finish_import(
        self,
        *,
        source: DataSource,
        document: SourceDocument | None,
        accepted_count: int,
        rejected_count: int,
        issues: list[DataQualityIssue],
    ) -> ImportResult:
        status = _import_status(accepted_count, rejected_count, len(issues))
        source.status = "failed" if status == "failed" else "ready"
        source.configuration = {
            **source.configuration,
            "import_status": status,
            "source_document_id": str(document.id) if document is not None else None,
            "accepted_count": accepted_count,
            "rejected_count": rejected_count,
            "issue_count": len(issues),
        }
        source.updated_at = datetime.now(UTC)
        await self.repository.create_audit_log(
            company_id=source.company_id,
            actor_id=self.actor_id,
            action="import.completed" if status != "failed" else "import.failed",
            entity_id=source.id,
            trace_id=self.trace_id or f"import:{source.id}",
            details={
                "import_type": source.configuration["import_type"],
                "status": status,
                "accepted_count": accepted_count,
                "rejected_count": rejected_count,
                "issue_count": len(issues),
                "is_synthetic": source.is_synthetic,
            },
        )
        await self.session.flush()
        await self.session.commit()
        return _build_import_result(source, document, issues)


def normalize_quantity(quantity: Decimal, source_unit: str, canonical_unit: str) -> Decimal:
    """Normalize allowlisted mass or energy units with Decimal-only arithmetic."""
    source_key = _normalize_unit(source_unit)
    target_key = _normalize_unit(canonical_unit)
    with localcontext() as context:
        context.prec = 50
        if source_key == target_key:
            normalized = quantity
        else:
            conversions = _ENERGY_TO_KWH if target_key in _ENERGY_TO_KWH else _MASS_TO_KG
            if source_key not in conversions or target_key not in conversions:
                raise UnsupportedUnitConversion("Unsupported unit conversion")
            normalized = quantity * conversions[source_key] / conversions[target_key]
        try:
            quantized = normalized.quantize(_SIX_PLACES, rounding=ROUND_HALF_EVEN)
        except InvalidOperation as error:
            raise NormalizedQuantityOutOfRange(
                "Normalized quantity is outside Numeric(24,6)."
            ) from error
    if quantized.copy_abs() >= Decimal(10) ** 18:
        raise NormalizedQuantityOutOfRange("Normalized quantity is outside Numeric(24,6).")
    return quantized


def _hourly_issue_context(source: DataSource) -> dict[str, Any]:
    if source.configuration.get("activity_kind") != "hourly_electricity":
        return {}
    return {
        "blocks_verification": True,
        "activity_kind": "hourly_electricity",
        "site_id": source.configuration["site_id"],
        "reporting_period_id": source.configuration["reporting_period_id"],
        "metric_definition_id": source.configuration["metric_definition_id"],
    }


def _import_request_hash(request: ActivityImportRequest | SupplierImportRequest) -> str:
    return _row_checksum(request.model_dump(mode="json", exclude={"idempotency_key"}))


def _normalize_unit(value: str) -> str:
    return " ".join(value.strip().casefold().replace("_", " ").replace("-", " ").split())


def _source_type(content_type: str) -> str:
    return "csv" if content_type == "text/csv" else "json"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _row_checksum(row: dict[str, Any]) -> str:
    content = json.dumps(
        row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return _sha256(content)


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime, UUID)):
        return str(value)
    return str(value)


def _json_safe_mapping(row: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(row, default=_json_default))


def _safe_row_key(candidate: str, checksum: str, row_number: int) -> str:
    candidate = candidate.strip()
    if not candidate:
        return checksum
    if len(candidate) <= 255:
        return candidate
    return f"invalid:{row_number}:{checksum}"


def _unique_persisted_row_key(
    candidate: str,
    *,
    checksum: str,
    row_number: int,
    persisted: set[str],
) -> str:
    if candidate not in persisted:
        return candidate
    attempt = 1
    while True:
        generated = f"collision:{row_number}:{attempt}:{checksum}"
        if generated not in persisted:
            return generated
        attempt += 1


def _validation_field(location: object) -> str | None:
    if not isinstance(location, (tuple, list)) or not location:
        return None
    return str(location[0])[:100]


def _import_status(accepted_count: int, rejected_count: int, issue_count: int) -> str:
    if accepted_count == 0 and (rejected_count > 0 or issue_count > 0):
        return "failed"
    if rejected_count > 0 or issue_count > 0:
        return "completed_with_errors"
    return "completed"


def _build_import_result(
    source: DataSource,
    document: SourceDocument | None,
    issues: list[DataQualityIssue],
) -> ImportResult:
    configuration = source.configuration
    # PostgreSQL transaction timestamps can tie. Match the persisted query's
    # UUID tie-break before truncation so the first response and retries agree.
    inline_issues = sorted(issues, key=lambda issue: (issue.created_at, issue.id))[
        :MAX_INLINE_ISSUES
    ]
    issue_count = int(configuration.get("issue_count", len(issues)))
    return ImportResult(
        import_id=source.id,
        data_source_id=source.id,
        source_document_id=document.id if document is not None else None,
        import_type=configuration["import_type"],
        status=configuration.get("import_status", "processing"),
        accepted_count=int(configuration.get("accepted_count", 0)),
        rejected_count=int(configuration.get("rejected_count", 0)),
        issue_count=issue_count,
        returned_issue_count=len(inline_issues),
        issues_truncated=issue_count > len(inline_issues),
        is_synthetic=source.is_synthetic,
        issues=[_to_issue_read(issue) for issue in inline_issues],
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _to_issue_read(issue: DataQualityIssue) -> DataQualityIssueRead:
    raw_import_id = issue.details.get("import_id")
    try:
        import_id = UUID(str(raw_import_id)) if raw_import_id is not None else None
    except ValueError:
        import_id = None
    raw_row_number = issue.details.get("row_number")
    row_number = raw_row_number if isinstance(raw_row_number, int) else None
    return DataQualityIssueRead(
        id=issue.id,
        company_id=issue.company_id,
        raw_activity_record_id=issue.raw_activity_record_id,
        activity_record_id=issue.activity_record_id,
        import_id=import_id,
        row_number=row_number,
        issue_type=issue.issue_type,
        code=issue.code,
        severity=issue.severity,
        field_name=issue.field_name,
        message=issue.message,
        status=issue.status,
        details=issue.details,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
    )
