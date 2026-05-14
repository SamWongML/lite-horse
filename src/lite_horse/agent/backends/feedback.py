"""``FeedbackSink`` Protocol — tenant-safe outcome / feedback writes.

A per-turn channel for the outcome classifier, the HTTP feedback
endpoint, and the regex-marker fallback in
``EvolutionHook`` to land structured rows that the curator + GEPA loop
read back. The shape is intentionally narrow: one write method
(``record``) and two reads (``latest_for_turn``, ``rating_stats``) so
the cloud + local impls stay symmetric.

Cloud impl writes to ``turn_outcomes`` via :class:`TurnOutcomeRepo`;
local impl appends NDJSON lines to ``~/.litehorse/feedback.log`` and
parses them back for the reads. Both pass the same dataclasses so the
caller never branches on backend type.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

OutcomeSource = Literal["classifier", "user_explicit", "regex_marker"]


@dataclass(frozen=True)
class OutcomeRecord:
    """The slice of ``turn_outcomes`` callers see across the backend seam."""

    session_id: str
    turn_id: str
    source: OutcomeSource
    rating: int
    reason: str | None
    skill_slug: str | None
    ts_iso: str


@dataclass(frozen=True)
class OutcomeStats:
    """Per-skill rating aggregates the curator + classifier consult."""

    use_count: int
    success_count: int
    error_count: int


@runtime_checkable
class FeedbackSink(Protocol):
    """Per-tenant feedback / outcome channel."""

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
        """Persist one outcome row and return the canonical record."""
        ...

    async def latest_for_turn(self, turn_id: str) -> OutcomeRecord | None:
        """Return the most-recent outcome for ``turn_id`` or ``None``."""
        ...

    async def rating_stats(self, *, skill_slug: str) -> OutcomeStats:
        """Aggregate (use, success, error) counters for one skill."""
        ...
