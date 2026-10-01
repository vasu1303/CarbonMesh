from __future__ import annotations

from typing import Any


class SourceUploadError(RuntimeError):
    """A safe, typed failure at the source-ingestion boundary."""

    code = "source_upload_error"
    status_code = 400
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        field_details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or type(self).code
        self.status_code = status_code or type(self).status_code
        self.retryable = type(self).retryable if retryable is None else retryable
        self.field_details = field_details or {}


class SourceContentError(SourceUploadError):
    code = "source_content_invalid"
    status_code = 422


class SourceTooLargeError(SourceUploadError):
    code = "source_too_large"
    status_code = 413


class SourceChecksumMismatchError(SourceUploadError):
    code = "source_checksum_mismatch"
    status_code = 422


class SourceContextNotFoundError(SourceUploadError):
    code = "source_context_not_found"
    status_code = 404


class SourceConflictError(SourceUploadError):
    code = "source_conflict"
    status_code = 409


class PdfProviderUnavailableError(SourceUploadError):
    """PDF support was requested but the optional bounded extractor is absent."""

    code = "provider_unavailable"
    status_code = 503


class UnsupportedDocumentError(SourceUploadError):
    """The supplied document cannot safely yield supported evidence text."""

    code = "unsupported"
    status_code = 422


class SourceStorageUnavailableError(SourceUploadError):
    code = "source_storage_unavailable"
    status_code = 503
    retryable = True
