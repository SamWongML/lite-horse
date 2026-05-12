"""``turn_outcomes`` repository — Phase 44.

Owns the SQL paths into ``turn_outcomes``: ``record`` (the only writer),
``latest_for_turn`` (consulted by ``EvolutionHook`` when deciding whether
to fire the refiner), and ``list_recent_for_skill`` / ``rating_stats``
which the curator + Phase 45's GEPA loop use to read the per-skill
outcome history.

The table is RLS-protected by the compound ``(user_id, agent_id)``
``tenant_isolation`` policy from the migration; ``BaseRepo`` is used as
the per-request session carrier and reads the GUC for tenant isolation.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, case, desc, func, select

from lite_horse.models.turn_outcome import TurnOutcome
from lite_horse.repositories.base import BaseRepo


@dataclass(frozen=True)
class OutcomeRow:
    """Public shape returned to callers (no SQLAlchemy leak)."""

    id: int
    session_id: str
    turn_id: str
    source: str
    rating: int
    reason: str | None
    skill_slug: str | None
    ts_iso: str


@dataclass(frozen=True)
class RatingStats:
    """Aggregate per-skill rating counts."""

    use_count: int
    success_count: int
    error_count: int


class TurnOutcomeRepo(BaseRepo):
    """turn_outcomes CRUD + read aggregates."""

    async def record(
        self,
        *,
        agent_id: str,
        session_id: str,
        turn_id: str,
        source: str,
        rating: int,
        reason: str | None = None,
        skill_slug: str | None = None,
    ) -> OutcomeRow:
        """Insert one outcome. Returns the persisted row."""
        user_id = UUID(await self.current_user_id())
        row = TurnOutcome(
            user_id=user_id,
            agent_id=UUID(agent_id),
            session_id=session_id,
            turn_id=UUID(turn_id),
            source=source,
            rating=int(rating),
            reason=reason,
            skill_slug=skill_slug,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return _row_to_outcome(row)

    async def latest_for_turn(self, turn_id: str) -> OutcomeRow | None:
        """Return the most-recent outcome row for one turn, or None."""
        user_id = UUID(await self.current_user_id())
        stmt = (
            select(TurnOutcome)
            .where(
                and_(
                    TurnOutcome.user_id == user_id,
                    TurnOutcome.turn_id == UUID(turn_id),
                )
            )
            .order_by(desc(TurnOutcome.ts))
            .limit(1)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _row_to_outcome(row) if row is not None else None

    async def list_recent_for_skill(
        self, *, skill_slug: str, limit: int = 20
    ) -> list[OutcomeRow]:
        """Most-recent-first outcomes for one skill (curator + GEPA)."""
        user_id = UUID(await self.current_user_id())
        stmt = (
            select(TurnOutcome)
            .where(
                and_(
                    TurnOutcome.user_id == user_id,
                    TurnOutcome.skill_slug == skill_slug,
                )
            )
            .order_by(desc(TurnOutcome.ts))
            .limit(int(limit))
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [_row_to_outcome(r) for r in rows]

    async def rating_stats(self, *, skill_slug: str) -> RatingStats:
        """Aggregate ratings into ``(use, success, error)`` counters."""
        user_id = UUID(await self.current_user_id())
        stmt = (
            select(
                func.count(TurnOutcome.id).label("uses"),
                func.coalesce(
                    func.sum(
                        case((TurnOutcome.rating == 1, 1), else_=0)
                    ),
                    0,
                ).label("successes"),
                func.coalesce(
                    func.sum(
                        case((TurnOutcome.rating == -1, 1), else_=0)
                    ),
                    0,
                ).label("errors"),
            )
            .where(
                and_(
                    TurnOutcome.user_id == user_id,
                    TurnOutcome.skill_slug == skill_slug,
                )
            )
        )
        row = (await self.session.execute(stmt)).one()
        return RatingStats(
            use_count=int(row.uses or 0),
            success_count=int(row.successes or 0),
            error_count=int(row.errors or 0),
        )


def _row_to_outcome(row: TurnOutcome) -> OutcomeRow:
    return OutcomeRow(
        id=int(row.id),
        session_id=str(row.session_id),
        turn_id=str(row.turn_id),
        source=str(row.source),
        rating=int(row.rating),
        reason=row.reason,
        skill_slug=row.skill_slug,
        ts_iso=row.ts.isoformat() if row.ts else "",
    )
