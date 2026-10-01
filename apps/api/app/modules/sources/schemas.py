from __future__ import annotations

import json
from datetime import datetime
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

MAX_ENCODED_CONTENT_CHARS = 1_500_000
MAX_METADATA_BYTES = 16_384
UNSAFE_METADATA_KEYS = frozenset(
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

SourceType = Literal["csv", "json", "pdf", "api", "synthetic"]
SourceContentType = Literal[
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/pdf",
]
SourceEncoding = Literal["utf-8", "base64"]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class SourceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


def _contains_unsafe_metadata(value: object) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).casefold() in UNSAFE_METADATA_KEYS:
                return True
            if _contains_unsafe_metadata(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_unsafe_metadata(item) for item in value)
    return False


def _validate_safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    if _contains_unsafe_metadata(value):
        raise ValueError("metadata contains a non-public field")
    return value


class SourceUploadRequest(SourceSchema):
    company_id: UUID
    site_id: UUID | None = None
    reporting_period_id: UUID | None = None
    actor_id: UUID | None = None
    source_name: Annotated[str, Field(min_length=1, max_length=200)]
    source_type: SourceType = "api"
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    content_type: SourceContentType
    encoding: SourceEncoding = "utf-8"
    content: Annotated[str, Field(min_length=1, max_length=MAX_ENCODED_CONTENT_CHARS)]
    checksum: Sha256 | None = None
    evidence_type: Annotated[str, Field(min_length=1, max_length=40)]
    metadata: dict[str, Any] = Field(default_factory=dict)
    external_reference: Annotated[str | None, Field(min_length=1, max_length=255)] = None
    is_synthetic: bool = False

    @field_validator("source_name", "filename", "evidence_type", "external_reference")
    @classmethod
    def strip_bounded_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("The value must contain non-whitespace characters.")
        return stripped

    @field_validator("metadata")
    @classmethod
    def bound_json_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("metadata must contain only finite JSON-native values") from error
        if len(encoded) > MAX_METADATA_BYTES:
            raise ValueError(f"metadata cannot exceed {MAX_METADATA_BYTES} encoded bytes")
        return _validate_safe_metadata(value)

    @model_validator(mode="after")
    def require_base64_for_pdf(self) -> SourceUploadRequest:
        if self.content_type == "application/pdf" and self.encoding != "base64":
            raise ValueError("PDF content must use base64 encoding.")
        return self


class DataSourceMetadata(SourceSchema):
    id: UUID
    company_id: UUID
    site_id: UUID | None
    name: str
    source_type: str
    status: str
    external_reference: str | None
    is_synthetic: bool


class SourceDocumentMetadata(SourceSchema):
    id: UUID
    data_source_id: UUID
    filename: str
    content_type: str
    checksum: Sha256
    version: int
    size_bytes: int
    storage_uri: str | None
    metadata: dict[str, Any]

    _metadata_is_safe = field_validator("metadata")(_validate_safe_metadata)


class EvidenceMetadata(SourceSchema):
    id: UUID
    source_document_id: UUID
    evidence_type: str
    locator: str
    checksum: Sha256
    metadata: dict[str, Any]
    embedding_model: str | None
    embedded_at: datetime | None

    _metadata_is_safe = field_validator("metadata")(_validate_safe_metadata)


class SourceUploadResponse(SourceSchema):
    replayed: bool
    ingestion_method_id: str
    extraction_method_id: str
    chunking_method_id: str
    embedding_model_id: str
    decoded_size_bytes: int
    evidence_count: int
    source: DataSourceMetadata
    document: SourceDocumentMetadata
    evidence: list[EvidenceMetadata]
