from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

from app.modules.sources.errors import (
    PdfProviderUnavailableError,
    SourceContentError,
    SourceTooLargeError,
    UnsupportedDocumentError,
)

MAX_SOURCE_BYTES = 1_048_576
MAX_EXTRACTED_CHARACTERS = 120_000
MAX_CHUNK_CHARACTERS = 1_200
CHUNK_OVERLAP_CHARACTERS = 150
MAX_EVIDENCE_CHUNKS = 128

TEXT_EXTRACTION_METHOD_ID = "carbonmesh-text-utf8-v1"
JSON_EXTRACTION_METHOD_ID = "carbonmesh-json-utf8-v1"
PDF_EXTRACTION_METHOD_ID = "carbonmesh-pypdf-v1"
CHUNKING_METHOD_ID = "carbonmesh-paragraph-1200-overlap-150-v1"


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    text: str
    extraction_method_id: str


@dataclass(frozen=True, slots=True)
class TextChunk:
    index: int
    start: int
    end: int
    locator: str
    text: str


class PdfPage(Protocol):
    def extract_text(self) -> str | None: ...


class PdfReader(Protocol):
    pages: list[PdfPage]
    is_encrypted: bool


def extraction_method_id(content_type: str) -> str:
    if content_type == "application/pdf":
        return PDF_EXTRACTION_METHOD_ID
    if content_type == "application/json":
        return JSON_EXTRACTION_METHOD_ID
    return TEXT_EXTRACTION_METHOD_ID


def _load_pdf_reader() -> Any:
    try:
        from pypdf import PdfReader as Reader
    except ModuleNotFoundError as error:
        raise PdfProviderUnavailableError(
            "PDF extraction is unavailable on this API instance."
        ) from error
    return Reader


def decode_content(*, content: str, encoding: str) -> bytes:
    if encoding == "utf-8":
        content_bytes = content.encode("utf-8")
    elif encoding == "base64":
        try:
            content_bytes = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError) as error:
            raise SourceContentError(
                "The source content is not valid base64.",
                field_details={"content": "invalid_base64"},
            ) from error
    else:
        raise SourceContentError(
            "The source encoding is unsupported.",
            field_details={"encoding": "unsupported"},
        )

    if len(content_bytes) > MAX_SOURCE_BYTES:
        raise SourceTooLargeError(
            f"Decoded source content cannot exceed {MAX_SOURCE_BYTES} bytes.",
            field_details={"content": "decoded_size_exceeded"},
        )
    if not content_bytes:
        raise SourceContentError(
            "The source content must not be empty.",
            field_details={"content": "empty"},
        )
    return content_bytes


def _decode_utf8(content_bytes: bytes) -> str:
    try:
        return content_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UnsupportedDocumentError(
            "The document is not valid UTF-8 text.",
            field_details={"content": "invalid_utf8"},
        ) from error


def _extract_pdf(content_bytes: bytes) -> str:
    reader_type = _load_pdf_reader()
    try:
        reader: PdfReader = reader_type(BytesIO(content_bytes))
        if reader.is_encrypted:
            raise UnsupportedDocumentError(
                "Encrypted PDF documents are unsupported.",
                field_details={"content": "encrypted_pdf"},
            )
        page_text = [page.extract_text() or "" for page in reader.pages]
    except UnsupportedDocumentError:
        raise
    except Exception as error:
        raise UnsupportedDocumentError(
            "The PDF document could not be safely extracted.",
            field_details={"content": "pdf_extraction_failed"},
        ) from error
    return "\n\n".join(page_text)


def _normalize_text(text: str) -> str:
    if "\x00" in text:
        raise UnsupportedDocumentError(
            "The document contains a NUL character and cannot be stored safely.",
            field_details={"content": "contains_nul"},
        )
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise UnsupportedDocumentError(
            "The document contains no extractable text.",
            field_details={"content": "no_extractable_text"},
        )
    if len(normalized) > MAX_EXTRACTED_CHARACTERS:
        raise SourceTooLargeError(
            f"Extracted text cannot exceed {MAX_EXTRACTED_CHARACTERS} characters.",
            field_details={"content": "extracted_size_exceeded"},
        )
    return normalized


def extract_content_bytes(*, content_bytes: bytes, content_type: str) -> ExtractedDocument:
    if content_type == "application/pdf":
        text = _extract_pdf(content_bytes)
    else:
        text = _decode_utf8(content_bytes)
        if content_type == "application/json":
            try:
                json.loads(
                    text,
                    parse_constant=lambda _value: (_ for _ in ()).throw(
                        ValueError("non-finite JSON constant")
                    ),
                )
            except (json.JSONDecodeError, ValueError) as error:
                raise UnsupportedDocumentError(
                    "The JSON document is invalid.",
                    field_details={"content": "invalid_json"},
                ) from error
    return ExtractedDocument(
        text=_normalize_text(text),
        extraction_method_id=extraction_method_id(content_type),
    )


def chunk_text(text: str) -> list[TextChunk]:
    """Split normalized text, preferring paragraph boundaries near each limit."""

    normalized = _normalize_text(text)
    chunks: list[TextChunk] = []
    start = 0
    total = len(normalized)
    while start < total:
        hard_end = min(start + MAX_CHUNK_CHARACTERS, total)
        end = hard_end
        if hard_end < total:
            # Prefer a paragraph break in the latter half of the bounded window.
            boundary_floor = start + max(
                CHUNK_OVERLAP_CHARACTERS + 1,
                MAX_CHUNK_CHARACTERS // 2,
            )
            boundary = normalized.rfind("\n\n", boundary_floor, hard_end + 1)
            if boundary != -1:
                end = boundary

        chunk_index = len(chunks) + 1
        chunk = TextChunk(
            index=chunk_index,
            start=start,
            end=end,
            locator=f"chunk:{chunk_index:06d}",
            text=normalized[start:end],
        )
        chunks.append(chunk)
        if len(chunks) > MAX_EVIDENCE_CHUNKS:
            raise SourceTooLargeError(
                f"A document cannot produce more than {MAX_EVIDENCE_CHUNKS} evidence chunks.",
                field_details={"content": "chunk_count_exceeded"},
            )
        if end == total:
            break
        start = end - CHUNK_OVERLAP_CHARACTERS

    return chunks
