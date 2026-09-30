from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProcurementError(Exception):
    """Safe, typed application error exposed by the procurement boundary."""

    code: str
    message: str
    status_code: int
    retryable: bool = False
    field_details: list[dict[str, Any]] = field(default_factory=list)

    def __str__(self) -> str:
        return self.message


def not_found(entity: str, identifier: object) -> ProcurementError:
    return ProcurementError(
        code=f"{entity}_not_found",
        message=f"The requested {entity.replace('_', ' ')} was not found.",
        status_code=404,
        field_details=[{"field": f"{entity}_id", "value": str(identifier)}],
    )
