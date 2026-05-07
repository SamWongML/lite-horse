"""``MemoryBackend`` Protocol — tenant-safe memory.md / user.md access.

The tool body never reaches into ``litehorse_home()`` or imports ``MemoryStore``
directly. It receives a ``MemoryBackend`` from ``RunContextWrapper.context``
and dispatches add / replace / remove / total_chars / char_limit / entries
through it. Two impls live alongside this file: ``memory_local`` wraps the
v0.4 :class:`MemoryStore` for the CLI / local dev path; ``memory_cloud``
wraps :class:`MemoryRepo` for the multi-tenant API path.
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

MemoryKind = Literal["memory", "user"]


class MemoryFull(Exception):  # noqa: N818
    """Raised when an add/replace would push the doc past its char limit."""

    def __init__(self, kind: MemoryKind, current: int, limit: int) -> None:
        super().__init__(
            f"{kind} memory at {current}/{limit} chars; entry would exceed limit."
        )
        self.kind = kind
        self.current = current
        self.limit = limit


class UnsafeMemoryContent(Exception):  # noqa: N818
    """Raised when an entry contains invisible Unicode or injection markers."""


@runtime_checkable
class MemoryBackend(Protocol):
    """Per-tenant memory document store: ``memory.md`` and ``user.md``."""

    async def get(self, kind: MemoryKind) -> str:
        """Return the document body (entries joined with the delimiter)."""
        ...

    async def entries(self, kind: MemoryKind) -> list[str]:
        """Return the list of entries currently stored."""
        ...

    async def total_chars(self, kind: MemoryKind) -> int:
        """Return total character count including entry delimiters."""
        ...

    def char_limit(self, kind: MemoryKind) -> int:
        """Return the per-document char ceiling."""
        ...

    async def add(self, kind: MemoryKind, content: str) -> None:
        """Append a new entry. Raises MemoryFull / UnsafeMemoryContent."""
        ...

    async def replace(self, kind: MemoryKind, old: str, new: str) -> None:
        """Replace one matching entry. Raises ValueError on non-unique match."""
        ...

    async def remove(self, kind: MemoryKind, old: str) -> None:
        """Remove one matching entry. Raises ValueError on non-unique match."""
        ...
