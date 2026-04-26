"""Filesystem-backed BlobStore — local dev + unit tests."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from lite_horse.storage.blob import BlobStore


class LocalBlobStore(BlobStore):
    """Stores blobs under a root directory. Keys map to relative paths.

    `presign_get` returns a `file://` URL — sufficient for tests; the
    public API never exposes these.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if key.startswith("/") or ".." in key.split("/"):
            raise ValueError(f"unsafe blob key: {key!r}")
        p = self._root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    async def put(
        self, key: str, body: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        self._path(key).write_bytes(body)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        return path.read_bytes()

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    async def presign_get(self, key: str, expires: int = 900) -> str:
        return f"file://{quote(str(self._path(key)))}"
