"""Cloud :class:`MemoryBackend` impl — wraps :class:`MemoryRepo`.

Each method opens its own short-lived ``db_session(user_id)`` transaction.
The request connection is *not* pinned for the duration of the agent run;
this matches the v0.5 contract that backends route every write through a
fresh tenant-scoped session.

Documents are stored whole in ``user_documents``; entry-level mutation
(used by the ``memory`` tool) is implemented here by reading the body,
splitting on ``ENTRY_DELIMITER``, mutating, and writing back.
"""
from __future__ import annotations

from lite_horse.agent.backends.memory import (
    MemoryBackend,
    MemoryFull,
    MemoryKind,
    UnsafeMemoryContent,
)
from lite_horse.constants import (
    ENTRY_DELIMITER,
    MEMORY_CHAR_LIMIT,
    USER_PROFILE_CHAR_LIMIT,
)
from lite_horse.repositories.memory_repo import MemoryFull as _RepoMemoryFull
from lite_horse.repositories.memory_repo import MemoryRepo
from lite_horse.repositories.memory_repo import (
    UnsafeMemoryContent as _RepoUnsafe,
)
from lite_horse.security.validators import UnsafeContent, check_untrusted
from lite_horse.storage.db import db_session

_KIND_DOC = {"memory": "memory.md", "user": "user.md"}
_KIND_LIMIT = {"memory": MEMORY_CHAR_LIMIT, "user": USER_PROFILE_CHAR_LIMIT}


def _split_entries(text: str) -> list[str]:
    if not text:
        return []
    return [e.strip() for e in text.split(ENTRY_DELIMITER.strip()) if e.strip()]


def _join_entries(entries: list[str]) -> str:
    return ENTRY_DELIMITER.join(entries)


def _total_chars(entries: list[str]) -> int:
    return sum(len(e) for e in entries) + max(0, len(entries) - 1) * len(
        ENTRY_DELIMITER
    )


class MemoryCloudBackend(MemoryBackend):
    """Postgres-backed memory store for one user.

    The instance is cheap; build one per turn from the agent factory.
    Every method opens a fresh transaction so the request DB connection
    is free to serve the streaming turn.
    """

    def __init__(self, *, user_id: str) -> None:
        self.user_id = user_id

    async def get(self, kind: MemoryKind) -> str:
        async with db_session(self.user_id) as session:
            return await MemoryRepo(session).get(_KIND_DOC[kind])

    async def entries(self, kind: MemoryKind) -> list[str]:
        return _split_entries(await self.get(kind))

    async def total_chars(self, kind: MemoryKind) -> int:
        return _total_chars(await self.entries(kind))

    def char_limit(self, kind: MemoryKind) -> int:
        return _KIND_LIMIT[kind]

    async def add(self, kind: MemoryKind, content: str) -> None:
        if not content.strip():
            raise ValueError("empty memory entry")
        try:
            check_untrusted(content)
        except UnsafeContent as e:
            raise UnsafeMemoryContent(str(e)) from e
        async with db_session(self.user_id) as session:
            repo = MemoryRepo(session)
            body = await repo.get(_KIND_DOC[kind])
            entries = _split_entries(body)
            if content in entries:
                return
            new_total = _total_chars(entries) + len(content) + (
                len(ENTRY_DELIMITER) if entries else 0
            )
            limit = _KIND_LIMIT[kind]
            if new_total > limit:
                raise MemoryFull(kind, _total_chars(entries), limit)
            entries.append(content)
            await self._put(repo, kind, entries)

    async def replace(self, kind: MemoryKind, old: str, new: str) -> None:
        if not new.strip():
            raise ValueError("empty memory entry")
        try:
            check_untrusted(new)
        except UnsafeContent as e:
            raise UnsafeMemoryContent(str(e)) from e
        async with db_session(self.user_id) as session:
            repo = MemoryRepo(session)
            entries = _split_entries(await repo.get(_KIND_DOC[kind]))
            matches = [i for i, e in enumerate(entries) if old in e]
            if not matches:
                raise ValueError(f"no entry matches substring: {old!r}")
            if len(matches) > 1:
                raise ValueError(
                    f"substring {old!r} matches {len(matches)} entries; "
                    "be more specific"
                )
            entries[matches[0]] = new
            limit = _KIND_LIMIT[kind]
            if _total_chars(entries) > limit:
                raise MemoryFull(kind, _total_chars(entries), limit)
            await self._put(repo, kind, entries)

    async def remove(self, kind: MemoryKind, old: str) -> None:
        async with db_session(self.user_id) as session:
            repo = MemoryRepo(session)
            entries = _split_entries(await repo.get(_KIND_DOC[kind]))
            matches = [i for i, e in enumerate(entries) if old in e]
            if not matches:
                raise ValueError(f"no entry matches substring: {old!r}")
            if len(matches) > 1:
                raise ValueError(
                    f"substring {old!r} matches {len(matches)} entries; "
                    "be more specific"
                )
            entries.pop(matches[0])
            await self._put(repo, kind, entries)

    @staticmethod
    async def _put(
        repo: MemoryRepo, kind: MemoryKind, entries: list[str]
    ) -> None:
        body = _join_entries(entries)
        try:
            await repo.put(_KIND_DOC[kind], body)
        except _RepoMemoryFull as e:
            raise MemoryFull(kind, e.attempted, e.limit) from e
        except _RepoUnsafe as e:
            raise UnsafeMemoryContent(str(e)) from e
