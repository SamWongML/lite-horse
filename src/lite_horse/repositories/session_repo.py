"""Postgres-backed session repository.

Methods mirror the v0.3 `SessionDB` surface for sessions, ported async
and tenant-scoped on every query. The `user_id` is read from the
`app.user_id` GUC set by `storage.db.db_session`; RLS on the `sessions`
table backs that up.

Timestamps are TIMESTAMPTZ in Postgres, but the public dict shape keeps
them as float epoch seconds so callers (REPL picker, evolve trace miner)
that previously consumed the SQLite `REAL` rows don't have to re-render.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from lite_horse.models.message import Message
from lite_horse.models.session import Session
from lite_horse.repositories.base import BaseRepo


def _to_epoch(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


class SessionRepo(BaseRepo):
    """Per-request session CRUD. One instance per `AsyncSession`."""

    async def create_session(
        self,
        *,
        session_id: str,
        source: str,
        model: str | None = None,
    ) -> None:
        """Idempotent: re-creating an existing session is a no-op.

        v0.3's `INSERT OR IGNORE` semantics ported via `ON CONFLICT DO
        NOTHING`. The session row is owned by the current `app.user_id`
        GUC (a foreign key into `users`).
        """
        user_id = UUID(await self.current_user_id())
        stmt = (
            pg_insert(Session)
            .values(
                id=session_id,
                user_id=user_id,
                source=source,
                model=model,
            )
            .on_conflict_do_nothing(index_elements=[Session.id])
        )
        await self.session.execute(stmt)

    async def end_session(
        self, session_id: str, *, end_reason: str = "user_exit"
    ) -> None:
        user_id = UUID(await self.current_user_id())
        stmt = (
            update(Session)
            .where(and_(Session.id == session_id, Session.user_id == user_id))
            .values(ended_at=func.now(), end_reason=end_reason)
        )
        await self.session.execute(stmt)

    async def get_session_meta(self, session_id: str) -> dict[str, Any] | None:
        """Return one row's metadata (no messages). None if not owned."""
        user_id = UUID(await self.current_user_id())
        stmt = select(Session).where(
            and_(Session.id == session_id, Session.user_id == user_id)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return _row_to_dict(row)

    async def list_recent_sessions(
        self, *, limit: int = 20, include_ended: bool = True
    ) -> list[dict[str, Any]]:
        user_id = UUID(await self.current_user_id())
        stmt = select(Session).where(Session.user_id == user_id)
        if not include_ended:
            stmt = stmt.where(Session.ended_at.is_(None))
        stmt = stmt.order_by(Session.started_at.desc()).limit(int(limit))
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_row_to_dict(r) for r in rows]

    async def delete_sessions_ended_before(self, cutoff_ts: float) -> int:
        """Reclaim sessions whose `ended_at` precedes `cutoff_ts`.

        Returns the count deleted. Unfinished sessions are left alone.
        Cascades on `messages` are enforced by the FK constraint.
        """
        user_id = UUID(await self.current_user_id())
        cutoff = datetime.fromtimestamp(float(cutoff_ts), tz=UTC)
        # Collect ids first so the count is reliable across DBs.
        sel = select(Session.id).where(
            and_(
                Session.user_id == user_id,
                Session.ended_at.is_not(None),
                Session.ended_at < cutoff,
            )
        )
        ids = list((await self.session.execute(sel)).scalars().all())
        if not ids:
            return 0
        # Cascade is wired on the model FK, but be explicit for clarity.
        await self.session.execute(
            delete(Message).where(
                and_(Message.user_id == user_id, Message.session_id.in_(ids))
            )
        )
        await self.session.execute(
            delete(Session).where(
                and_(Session.user_id == user_id, Session.id.in_(ids))
            )
        )
        return len(ids)

    async def find_session_by_prefix(self, prefix: str) -> str | None:
        """Resolve a session-key prefix to a unique id; raise on ambiguity."""
        if not prefix:
            return None
        user_id = UUID(await self.current_user_id())
        stmt = (
            select(Session.id)
            .where(
                and_(
                    Session.user_id == user_id,
                    Session.id.like(f"{prefix}%"),
                )
            )
            .order_by(Session.started_at.desc())
            .limit(2)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(f"ambiguous session prefix: {prefix!r}")
        return str(rows[0])


def _row_to_dict(r: Session) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "source": r.source,
        "user_id": str(r.user_id),
        "model": r.model,
        "started_at": _to_epoch(r.started_at),
        "ended_at": _to_epoch(r.ended_at),
        "end_reason": r.end_reason,
        "message_count": int(r.message_count or 0),
        "tool_call_count": int(r.tool_call_count or 0),
        "input_tokens": int(r.input_tokens or 0),
        "output_tokens": int(r.output_tokens or 0),
        "title": r.title,
    }
