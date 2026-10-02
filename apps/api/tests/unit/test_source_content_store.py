import hashlib
from uuid import UUID

import pytest

from app.modules.sources.errors import SourceStorageUnavailableError
from app.modules.sources.storage import SourceContentStore


@pytest.mark.asyncio
async def test_original_bytes_are_preserved_and_tenant_isolated(tmp_path) -> None:
    store = SourceContentStore(tmp_path)
    content = b"%PDF-1.4\x00\xff original bytes"
    checksum = hashlib.sha256(content).hexdigest()
    company = UUID(int=1)
    uri = await store.put(company, checksum, content)
    assert uri == f"content://{company}/{checksum}"
    assert await store.read(company, checksum, len(content)) == content
    with pytest.raises(SourceStorageUnavailableError):
        await store.read(UUID(int=2), checksum, len(content))
    with pytest.raises(SourceStorageUnavailableError):
        await store.read(company, "../../outside", len(content))


@pytest.mark.asyncio
async def test_content_corruption_and_wrong_size_fail_closed(tmp_path) -> None:
    store = SourceContentStore(tmp_path)
    content = b"verified source"
    checksum = hashlib.sha256(content).hexdigest()
    company = UUID(int=1)
    await store.put(company, checksum, content)
    with pytest.raises(SourceStorageUnavailableError):
        await store.read(company, checksum, len(content) + 1)
    (tmp_path / str(company) / checksum).write_bytes(b"corrupt source")
    with pytest.raises(SourceStorageUnavailableError):
        await store.read(company, checksum, len(content))
    with pytest.raises(SourceStorageUnavailableError):
        await store.put(company, checksum, b"different source")
