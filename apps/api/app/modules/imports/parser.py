from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


class ImportPayloadError(ValueError):
    """The supplied document cannot be parsed into row objects."""


@dataclass(frozen=True, slots=True)
class ParsedImport:
    records: list[dict[str, Any]]
    row_numbers: list[int]
    document_bytes: bytes


def document_bytes(content: str | list[Any] | dict[str, Any]) -> bytes:
    """Return stable UTF-8 bytes for checksum and document identity."""
    if isinstance(content, str):
        return content.encode("utf-8")
    return json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def parse_import_content(
    content: str | list[Any] | dict[str, Any], content_type: str
) -> ParsedImport:
    payload_bytes = document_bytes(content)
    if content_type == "text/csv":
        if not isinstance(content, str):
            raise ImportPayloadError("CSV content must be supplied as text.")
        records, row_numbers = _parse_csv(content)
    elif content_type == "application/json":
        records = _parse_json(content)
        row_numbers = list(range(1, len(records) + 1))
    else:  # pragma: no cover - request contract guards this branch
        raise ImportPayloadError(f"Unsupported content type: {content_type}")
    return ParsedImport(
        records=records,
        row_numbers=row_numbers,
        document_bytes=payload_bytes,
    )


def _parse_csv(content: str) -> tuple[list[dict[str, Any]], list[int]]:
    try:
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff"), newline=""))
        if reader.fieldnames is None:
            return [], []
        records: list[dict[str, Any]] = []
        row_numbers: list[int] = []
        for raw_row in reader:
            row: dict[str, Any] = {}
            for key, value in raw_row.items():
                if key is None:
                    row["_extra_columns"] = value
                else:
                    row[key.strip()] = value
            records.append(row)
            row_numbers.append(reader.line_num)
        return records, row_numbers
    except (csv.Error, UnicodeError) as error:
        raise ImportPayloadError("CSV content could not be parsed.") from error


def _parse_json(content: str | list[Any] | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(content, str):
        try:
            payload: Any = json.loads(content, parse_float=Decimal, parse_int=Decimal)
        except (json.JSONDecodeError, UnicodeError) as error:
            raise ImportPayloadError("JSON content could not be parsed.") from error
    else:
        payload = content

    if isinstance(payload, dict) and "records" in payload:
        payload = payload["records"]
    elif isinstance(payload, dict) and "products" in payload:
        payload = payload["products"]
    elif isinstance(payload, dict):
        payload = [payload]

    if not isinstance(payload, list):
        raise ImportPayloadError("JSON content must contain an object or a list of objects.")

    records: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ImportPayloadError(f"JSON row {index} must be an object.")
        records.append(item)
    return records


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)
