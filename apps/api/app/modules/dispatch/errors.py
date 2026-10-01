from __future__ import annotations


class DispatchError(RuntimeError):
    code = "dispatch_error"
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


class DispatchNotFoundError(DispatchError):
    code = "dispatch_not_found"
    status_code = 404


class DispatchConflictError(DispatchError):
    code = "dispatch_conflict"
    status_code = 409


class DispatchValidationError(DispatchError):
    code = "dispatch_validation_failed"
    status_code = 422


class ForecastProviderError(DispatchError):
    code = "dispatch_forecast_provider_unavailable"
    status_code = 503
    retryable = True
