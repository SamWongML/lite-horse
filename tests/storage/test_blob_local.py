"""LocalBlobStore Protocol contract."""
from __future__ import annotations

from pathlib import Path

import pytest

from lite_horse.storage.blob import BlobStore
from lite_horse.storage.blob_local import LocalBlobStore


@pytest.fixture()
def store(tmp_path: Path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path / "blobs")


async def test_satisfies_protocol(store: LocalBlobStore) -> None:
    assert isinstance(store, BlobStore)


async def test_put_get_roundtrip(store: LocalBlobStore) -> None:
    await store.put("a/b/c.bin", b"hello", "application/octet-stream")
    assert await store.get("a/b/c.bin") == b"hello"


async def test_get_missing_raises(store: LocalBlobStore) -> None:
    with pytest.raises(FileNotFoundError):
        await store.get("nope")


async def test_delete_is_idempotent(store: LocalBlobStore) -> None:
    await store.put("x", b"y")
    await store.delete("x")
    await store.delete("x")  # second call no-op
    with pytest.raises(FileNotFoundError):
        await store.get("x")


async def test_presign_returns_file_url(store: LocalBlobStore) -> None:
    await store.put("doc.txt", b"hi")
    url = await store.presign_get("doc.txt")
    assert url.startswith("file://")


async def test_rejects_path_traversal(store: LocalBlobStore) -> None:
    with pytest.raises(ValueError):
        await store.put("../escape", b"")
    with pytest.raises(ValueError):
        await store.put("/abs", b"")
