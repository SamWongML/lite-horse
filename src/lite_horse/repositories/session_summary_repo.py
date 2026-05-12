"""``session_summaries`` repository — Phase 43.

Persists the side-agent's per-session blurb plus the per-tenant lookup
helpers the prompt-assembly path needs:

* :meth:`upsert` — write or replace the summary for one session.
* :meth:`get` — fetch the summary row for one session.
* :meth:`list_recent` — most-recent-first, used to inject the last-N
  summaries into the next session's system prompt.
* :meth:`find_idle_sessions` — scheduler-side scan for completed-but-
  unsummarised sessions (no ``session_summaries`` row, last message
  older than the configurable cutoff).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from lite_horse.models.message import Message
from lite_horse.models.session import Session
from lite_horse.models.session_summary import SessionSummary
from lite_horse.repositories.base import BaseRepo


@dataclass(frozen=True)
class SummaryRow:
    """Public shape returned by :class:`SessionSummaryRepo` reads."""

    session_id: str
    summary: str
    topic: str | None
    generated_at_iso: str
    generator: str


@dataclass(frozen=True)
class IdleSession:
    """One ``(session_id, agent_id)`` due for summarisation."""

    session_id: str
    user_id: str
    agent_id: str | None


class SessionSummaryRepo(BaseRepo):
    """session_summaries CRUD + idle-session discovery."""

    async def upsert(
        self,
        *,
        session_id: str,
        agent_id: str,
        summary: str,
        topic: str | None,
        generator: str,
    ) -> None:
        """Write or replace the summary row for one session."""
        user_id = UUID(await self.current_user_id())
        stmt = (
            pg_insert(SessionSummary)
            .values(
                session_id=session_id,
                user_id=user_id,
                agent_id=UUID(agent_id),
                summary=summary,
                topic=topic,
                generator=generator,
            )
            .on_conflict_do_update(
                index_elements=[SessionSummary.session_id],
                set_={
                    "summary": summary,
                    "topic": topic,
                    "generator": generator,
                    "generated_at": func.now(),
                },
            )
        )
        await self.session.execute(stmt)

    async def get(self, session_id: str) -> SummaryRow | None:
        user_id = UUID(await self.current_user_id())
        stmt = select(SessionSummary).where(
            and_(
                SessionSummary.session_id == session_id,
                SessionSummary.user_id == user_id,
            )
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return _row_to_summary(row)

    async def list_recent(
        self,
        *,
        agent_id: str | None,
        exclude_session_id: str | None = None,
        limit: int = 3,
    ) -> list[SummaryRow]:
        """Most-recent-first summaries for one agent (or all if None)."""
        user_id = UUID(await self.current_user_id())
        stmt = select(SessionSummary).where(SessionSummary.user_id == user_id)
        if agent_id is not None:
            stmt = stmt.where(SessionSummary.agent_id == UUID(agent_id))
        if exclude_session_id is not None:
            stmt = stmt.where(SessionSummary.session_id != exclude_session_id)
        stmt = stmt.order_by(desc(SessionSummary.generated_at)).limit(
            int(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [_row_to_summary(r) for r in rows]

    async def find_idle_sessions(
        self,
        *,
        idle_hours: int = 24,
        limit: int = 100,
        now: datetime | None = None,
    ) -> list[IdleSession]:
        """Return sessions with no summary row + last activity past cutoff.

        Cross-tenant scan: the scheduler runs without ``app.user_id`` set
        so this query bypasses RLS via the empty-string fallback and
        emits one ``IdleSession`` per row for the worker to summarise
        under each owner's tenant. Sessions that already have a summary
        row are excluded.
        """
        moment = now or datetime.now(UTC)
        cutoff = moment - timedelta(hours=int(idle_hours))

        last_msg = (
            select(
                Message.session_id.label("sid"),
                func.max(Message.ts).label("last_ts"),
            )
            .group_by(Message.session_id)
            .subquery()
        )
        stmt = (
            select(
                Session.id.label("session_id"),
                Session.user_id.label("user_id"),
                Session.agent_id.label("agent_id"),
            )
            .join(last_msg, last_msg.c.sid == Session.id, isouter=True)
            .outerjoin(
                SessionSummary, SessionSummary.session_id == Session.id
            )
            .where(SessionSummary.session_id.is_(None))
            .where(
                func.coalesce(last_msg.c.last_ts, Session.started_at) < cutoff
            )
            .order_by(Session.started_at)
            .limit(int(limit))
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            IdleSession(
                session_id=str(r.session_id),
                user_id=str(r.user_id),
                agent_id=str(r.agent_id) if r.agent_id else None,
            )
            for r in rows
        ]


def _row_to_summary(row: SessionSummary) -> SummaryRow:
    return SummaryRow(
        session_id=str(row.session_id),
        summary=row.summary,
        topic=row.topic,
        generated_at_iso=(
            row.generated_at.isoformat() if row.generated_at else ""
        ),
        generator=row.generator,
    )
