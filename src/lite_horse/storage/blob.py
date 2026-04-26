"""BlobStore Protocol.

Cloud impl (`blob_s3.py`) targets S3 (or MinIO via `LITEHORSE_S3_ENDPOINT`).
Local impl (`blob_local.py`) targets the filesystem and is the default
for unit tests.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BlobStore(Protocol):
    """Async object storage. Keys are forward-slash-separated strings."""

    async def put(
        self,
        key: str,
        body: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        ...

    async def get(self, key: str) -> bytes:
        ...

    async def delete(self, key: str) -> None:
        ...

    async def presign_get(self, key: str, expires: int = 900) -> str:
        """Return a time-limited URL for direct download."""
        ...
