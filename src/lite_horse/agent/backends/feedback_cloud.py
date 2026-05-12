"""Cloud :class:`FeedbackSink` — wraps :class:`TurnOutcomeRepo`.

One short-lived ``db_session(user_id, agent_id)`` per call. ``record``
returns the canonical :class:`OutcomeRecord`; the two read methods
project the same shape from the repo's :class:`OutcomeRow`.
"""
from __future__ import annotations

from lite_horse.agent.backends.feedback import (
    FeedbackSink,
    OutcomeRecord,
    OutcomeSource,
    OutcomeStats,
)
from lite_horse.repositories.turn_outcome_repo import (
    OutcomeRow,
    TurnOutcomeRepo,
)
from lite_horse.storage.db import db_session


def _row_to_record(row: OutcomeRow) -> OutcomeRecord:
    return OutcomeRecord(
        session_id=row.session_id,
        turn_id=row.turn_id,
        source=row.source,  # type: ignore[arg-type]
        rating=row.rating,
        reason=row.reason,
        skill_slug=row.skill_slug,
        ts_iso=row.ts_iso,
    )


class FeedbackCloudBackend(FeedbackSink):
    """Postgres-backed feedback sink for one ``(user, agent)`` pair."""

    def __init__(self, *, user_id: str, agent_id: str | None) -> None:
        self.user_id = user_id
        self.agent_id = agent_id

    async def record(
        self,
        *,
        session_id: str,
        turn_id: str,
        source: OutcomeSource,
        rating: int,
        reason: str | None = None,
        skill_slug: str | None = None,
    ) -> OutcomeRecord:
        if self.agent_id is None:
            raise RuntimeError(
                "FeedbackCloudBackend.record requires agent_id; the cloud "
                "path always resolves an agent before a turn runs"
            )
        async with db_session(self.user_id, self.agent_id) as session:
            row = await TurnOutcomeRepo(session).record(
                agent_id=self.agent_id,
                session_id=session_id,
                turn_id=turn_id,
                source=source,
                rating=rating,
                reason=reason,
                skill_slug=skill_slug,
            )
        return _row_to_record(row)

    async def latest_for_turn(self, turn_id: str) -> OutcomeRecord | None:
        async with db_session(self.user_id, self.agent_id) as session:
            row = await TurnOutcomeRepo(session).latest_for_turn(turn_id)
        return _row_to_record(row) if row is not None else None

    async def rating_stats(self, *, skill_slug: str) -> OutcomeStats:
        async with db_session(self.user_id, self.agent_id) as session:
            stats = await TurnOutcomeRepo(session).rating_stats(
                skill_slug=skill_slug
            )
        return OutcomeStats(
            use_count=stats.use_count,
            success_count=stats.success_count,
            error_count=stats.error_count,
        )
