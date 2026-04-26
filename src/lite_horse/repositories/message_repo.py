"""Postgres-backed message repository with tsvector FTS.

Mirrors the v0.3 `SessionDB` message surface:

* `append_message` / `get_messages` / `pop_last_message` / `clear_session`
  / `copy_messages` ported async.
* `search_messages` uses `websearch_to_tsquery('simple', q)` (which
  parses the FTS5-style "bare AND, "quoted phrases", OR, -negate" syntax
  the existing tool prompt teaches the model) plus `ts_headline` for
  the `>>>match<<<` snippet shape callers already render.

`tsv` is a generated column on `messages` (DDL in Phase 31). We never
write to it directly; the GIN index `messages_tsv` makes the FTS lookup
fast.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Integer,
    and_,
    delete,
    desc,
    func,
    insert,
    literal,
    select,
    update,
)

from lite_horse.models.message import Message
from lite_horse.models.session import Session
from lite_horse.repositories.base import BaseRepo
from lite_horse.sessions.types import SearchHit

_HEADLINE_OPTS = "StartSel=>>>, StopSel=<<<, MaxWords=8, MinWords=1"


def _row_to_msg(r: Message) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": r.role}
    if r.content is not None:
        msg["content"] = r.content
    if r.tool_call_id:
        msg["tool_call_id"] = r.tool_call_id
    if r.tool_calls:
        msg["tool_calls"] = list(r.tool_calls)
    if r.tool_name:
        msg["name"] = r.tool_name
    return msg


def _to_epoch(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


class MessageRepo(BaseRepo):
    """Per-request message CRUD + FTS. One instance per `AsyncSession`."""

    async def append_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str | None,
        tool_call_id: str | None = None,
        tool_calls: Iterable[dict[str, Any]] | None = None,
        tool_name: str | None = None,
    ) -> int:
        user_id = UUID(await self.current_user_id())
        tool_calls_list = list(tool_calls) if tool_calls else None
        stmt = (
            insert(Message)
            .values(
                user_id=user_id,
                session_id=session_id,
                role=role,
                content=content,
                tool_call_id=tool_call_id,
                tool_calls=tool_calls_list,
                tool_name=tool_name,
            )
            .returning(Message.id)
        )
        result = await self.session.execute(stmt)
        new_id = int(result.scalar_one())
        await self.session.execute(
            update(Session)
            .where(and_(Session.id == session_id, Session.user_id == user_id))
            .values(message_count=Session.message_count + 1)
        )
        return new_id

    async def get_messages(
        self, session_id: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        user_id = UUID(await self.current_user_id())
        base = (
            select(Message)
            .where(
                and_(
                    Message.user_id == user_id,
                    Message.session_id == session_id,
                )
            )
        )
        if limit is None:
            stmt = base.order_by(Message.ts, Message.id)
            rows = list((await self.session.execute(stmt)).scalars().all())
            return [_row_to_msg(r) for r in rows]

        # Latest-N, returned earliest-first (SDK Session.get_items contract).
        latest = base.order_by(desc(Message.ts), desc(Message.id)).limit(int(limit))
        rows = list((await self.session.execute(latest)).scalars().all())
        rows.reverse()
        return [_row_to_msg(r) for r in rows]

    async def pop_last_message(self, session_id: str) -> dict[str, Any] | None:
        user_id = UUID(await self.current_user_id())
        stmt = (
            select(Message)
            .where(
                and_(
                    Message.user_id == user_id,
                    Message.session_id == session_id,
                )
            )
            .order_by(desc(Message.ts), desc(Message.id))
            .limit(1)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        msg = _row_to_msg(row)
        await self.session.execute(
            delete(Message).where(
                and_(Message.user_id == user_id, Message.id == row.id)
            )
        )
        await self.session.execute(
            update(Session)
            .where(and_(Session.id == session_id, Session.user_id == user_id))
            .values(
                message_count=func.greatest(Session.message_count - 1, literal(0))
            )
        )
        return msg

    async def clear_session(self, session_id: str) -> None:
        user_id = UUID(await self.current_user_id())
        await self.session.execute(
            delete(Message).where(
                and_(
                    Message.user_id == user_id,
                    Message.session_id == session_id,
                )
            )
        )
        await self.session.execute(
            update(Session)
            .where(and_(Session.id == session_id, Session.user_id == user_id))
            .values(message_count=0)
        )

    async def copy_messages(
        self, *, src_session_id: str, dst_session_id: str
    ) -> int:
        """Copy every message from `src` to `dst`. Returns count copied.

        Caller must have already created the destination session row.
        Timestamps and roles are preserved; ids are regenerated by the
        new identity inserts. Used by `/fork`.
        """
        user_id = UUID(await self.current_user_id())
        src_stmt = (
            select(Message)
            .where(
                and_(
                    Message.user_id == user_id,
                    Message.session_id == src_session_id,
                )
            )
            .order_by(Message.ts, Message.id)
        )
        rows = list((await self.session.execute(src_stmt)).scalars().all())
        if not rows:
            return 0
        await self.session.execute(
            insert(Message),
            [
                {
                    "user_id": user_id,
                    "session_id": dst_session_id,
                    "role": r.role,
                    "content": r.content,
                    "tool_call_id": r.tool_call_id,
                    "tool_calls": r.tool_calls,
                    "tool_name": r.tool_name,
                    "ts": r.ts,
                }
                for r in rows
            ],
        )
        await self.session.execute(
            update(Session)
            .where(
                and_(Session.id == dst_session_id, Session.user_id == user_id)
            )
            .values(message_count=len(rows))
        )
        return len(rows)

    async def search_messages(
        self,
        query: str,
        *,
        limit: int = 20,
        source_filter: list[str] | None = None,
        exclude_sources: list[str] | None = None,
        role_filter: list[str] | None = None,
    ) -> list[SearchHit]:
        """Tenant-scoped FTS over `messages.tsv` with a snippet headline."""
        q = query.strip()
        if not q:
            return []
        user_id = UUID(await self.current_user_id())
        ts_query = func.websearch_to_tsquery("simple", q)
        snippet = func.ts_headline(
            "simple", func.coalesce(Message.content, ""), ts_query, _HEADLINE_OPTS
        )
        stmt = (
            select(
                Message.id.label("id"),
                Message.session_id.label("session_id"),
                Message.role.label("role"),
                func.extract("epoch", Message.ts).cast(Integer).label("ts_epoch"),
                snippet.label("snippet"),
                Session.source.label("source"),
            )
            .join(Session, Session.id == Message.session_id)
            .where(
                and_(
                    Message.user_id == user_id,
                    Session.user_id == user_id,
                    Message.tsv.op("@@")(ts_query),
                )
            )
        )
        if source_filter:
            stmt = stmt.where(Session.source.in_(list(source_filter)))
        if exclude_sources:
            stmt = stmt.where(Session.source.not_in(list(exclude_sources)))
        if role_filter:
            stmt = stmt.where(Message.role.in_(list(role_filter)))
        stmt = stmt.order_by(desc(Message.ts)).limit(int(limit))
        rows = (await self.session.execute(stmt)).all()
        return [
            SearchHit(
                id=int(r.id),
                session_id=str(r.session_id),
                role=str(r.role),
                timestamp=float(r.ts_epoch),
                snippet=str(r.snippet),
                source=str(r.source),
            )
            for r in rows
        ]
