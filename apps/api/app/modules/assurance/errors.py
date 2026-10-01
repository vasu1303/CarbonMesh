from __future__ import annotations


class AssuranceError(RuntimeError):
    """Safe typed error exposed by the Assurance application boundary."""

    code = "assurance_error"
    status_code = 400
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        field_details: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or type(self).code
        self.status_code = status_code or type(self).status_code
        self.retryable = type(self).retryable if retryable is None else retryable
        self.field_details = field_details or {}


class AssuranceNotFoundError(AssuranceError):
    code = "assurance_not_found"
    status_code = 404


class AssuranceConflictError(AssuranceError):
    code = "assurance_conflict"
    status_code = 409


class AssuranceValidationError(AssuranceError):
    code = "assurance_validation_failed"
    status_code = 422


class AssuranceStaleError(AssuranceError):
    code = "assurance_stale"
    status_code = 409
