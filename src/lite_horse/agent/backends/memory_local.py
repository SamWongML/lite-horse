"""Local-FS :class:`MemoryBackend` impl — wraps :class:`MemoryStore`.

Used by the CLI / single-user path and by tests. State lives at
``~/.litehorse/memories/{MEMORY,USER}.md``.
"""
from __future__ import annotations

from lite_horse.agent.backends.memory import (
    MemoryBackend,
    MemoryFull,
    MemoryKind,
    UnsafeMemoryContent,
)
from lite_horse.constants import ENTRY_DELIMITER
from lite_horse.memory.store import MemoryFull as _LocalMemoryFull
from lite_horse.memory.store import MemoryStore
from lite_horse.memory.store import UnsafeMemoryContent as _LocalUnsafe


def _store_for(kind: MemoryKind) -> MemoryStore:
    return MemoryStore.for_memory() if kind == "memory" else MemoryStore.for_user()


class MemoryLocalBackend(MemoryBackend):
    """File-backed memory using the v0.4 :class:`MemoryStore`."""

    async def get(self, kind: MemoryKind) -> str:
        store = _store_for(kind)
        return ENTRY_DELIMITER.join(store.entries())

    async def entries(self, kind: MemoryKind) -> list[str]:
        return _store_for(kind).entries()

    async def total_chars(self, kind: MemoryKind) -> int:
        return _store_for(kind).total_chars()

    def char_limit(self, kind: MemoryKind) -> int:
        return _store_for(kind).char_limit

    async def add(self, kind: MemoryKind, content: str) -> None:
        try:
            _store_for(kind).add(content)
        except _LocalMemoryFull as e:
            raise MemoryFull(kind, e.current, e.limit) from e
        except _LocalUnsafe as e:
            raise UnsafeMemoryContent(str(e)) from e

    async def replace(self, kind: MemoryKind, old: str, new: str) -> None:
        try:
            _store_for(kind).replace(old, new)
        except _LocalMemoryFull as e:
            raise MemoryFull(kind, e.current, e.limit) from e
        except _LocalUnsafe as e:
            raise UnsafeMemoryContent(str(e)) from e

    async def remove(self, kind: MemoryKind, old: str) -> None:
        _store_for(kind).remove(old)
