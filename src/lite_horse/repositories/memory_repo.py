"""Per-user `user_documents` repository — `memory.md` and `user.md`.

The v0.3 ``MemoryStore`` was filesystem-backed and char-bounded; this
repo is the cloud port. Both documents are mutable in place (no version
history), tenant-scoped via the ``app.user_id`` GUC + RLS, and every
write runs through ``check_untrusted`` so injection patterns or
invisible Unicode can never reach the system prompt.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from lite_horse.models.user_document import UserDocument
from lite_horse.repositories.base import BaseRepo
from lite_horse.security.validators import UnsafeContent, check_untrusted

# Plan-locked char ceilings. memory.md is the agent-managed scratchpad;
# user.md is the human-edited profile. Both feed the system prompt, so
# the cap also bounds prompt growth.
MEMORY_MD_CHAR_LIMIT = 2400
USER_MD_CHAR_LIMIT = 1500

_VALID_KINDS = ("memory.md", "user.md")
_LIMITS = {"memory.md": MEMORY_MD_CHAR_LIMIT, "user.md": USER_MD_CHAR_LIMIT}


class MemoryFull(Exception):  # noqa: N818
    """Raised when a put would exceed the per-document char limit."""

    def __init__(self, kind: str, attempted: int, limit: int) -> None:
        super().__init__(
            f"{kind} would be {attempted} chars (limit {limit})."
        )
        self.kind = kind
        self.attempted = attempted
        self.limit = limit


class UnsafeMemoryContent(UnsafeContent):
    """Raised when content contains invisible Unicode or injection patterns."""


class MemoryRepo(BaseRepo):
    """user_documents CRUD. One instance per ``AsyncSession``."""

    async def get(self, kind: str) -> str:
        """Return the document body, or empty string if it has never been written."""
        if kind not in _VALID_KINDS:
            raise ValueError(f"unknown memory kind: {kind!r}")
        user_id = UUID(await self.current_user_id())
        stmt = select(UserDocument.content).where(
            and_(UserDocument.user_id == user_id, UserDocument.kind == kind)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return row or ""

    async def put(self, kind: str, content: str) -> None:
        """Replace the document body. Idempotent; bumps ``version`` on write."""
        if kind not in _VALID_KINDS:
            raise ValueError(f"unknown memory kind: {kind!r}")
        limit = _LIMITS[kind]
        if len(content) > limit:
            raise MemoryFull(kind, len(content), limit)
        if content:
            try:
                check_untrusted(content)
            except UnsafeContent as exc:
                raise UnsafeMemoryContent(str(exc)) from exc
        user_id = UUID(await self.current_user_id())
        stmt = (
            pg_insert(UserDocument)
            .values(user_id=user_id, kind=kind, content=content, version=1)
            .on_conflict_do_update(
                index_elements=[UserDocument.user_id, UserDocument.kind],
                set_={
                    "content": content,
                    "version": UserDocument.version + 1,
                    "updated_at": func.now(),
                },
            )
        )
        await self.session.execute(stmt)
