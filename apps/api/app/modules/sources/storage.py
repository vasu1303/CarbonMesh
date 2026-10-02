"""Tenant-scoped, immutable local storage for bounded source document bytes."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID

from app.core.config import get_settings
from app.modules.sources.errors import SourceStorageUnavailableError


class SourceContentStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or get_settings().source_storage_dir).resolve()

    def _path(self, company_id: UUID, checksum: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
            raise SourceStorageUnavailableError("The source checksum is invalid.")
        target = (self.root / str(company_id) / checksum).resolve()
        if not target.is_relative_to(self.root):
            raise SourceStorageUnavailableError("The source content location is invalid.")
        return target

    async def put(self, company_id: UUID, checksum: str, content: bytes) -> str:
        if hashlib.sha256(content).hexdigest() != checksum:
            raise SourceStorageUnavailableError("Source content integrity validation failed.")

        def write() -> None:
            target = self._path(company_id, checksum)
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)

        try:
            await asyncio.to_thread(write)
        except OSError as error:
            raise SourceStorageUnavailableError("Source content storage is unavailable.") from error
        return f"content://{company_id}/{checksum}"

    async def read(self, company_id: UUID, checksum: str, size_bytes: int) -> bytes:
        def load() -> bytes:
            with self._path(company_id, checksum).open("rb") as stream:
                return stream.read(1024 * 1024 + 1)

        try:
            content = await asyncio.to_thread(load)
        except OSError as error:
            raise SourceStorageUnavailableError(
                "Original source content is unavailable."
            ) from error
        if len(content) != size_bytes or hashlib.sha256(content).hexdigest() != checksum:
            raise SourceStorageUnavailableError("Source content integrity validation failed.")
        return content
