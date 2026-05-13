"""Mine an eval set for one skill from ``turn_outcomes``.

Phase 45's GEPA loop needs concrete ``(user_request, expected_signal)``
cases to score variants against. The Phase 44 ``turn_outcomes`` table
already records ``(turn_id, skill_slug, rating, reason)`` for every
classified or user-flagged turn; this module materialises that into
the eval shape.

The cloud path joins ``turn_outcomes`` → ``messages`` to recover the
user prompt that originated each turn. The local CLI path receives a
list of pre-built cases from a fixture — the
:func:`mine_eval_set_from_outcomes` builder accepts already-resolved
prompts and is hermetic for tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lite_horse.constants import GEPA_MAX_TRAJECTORIES, GEPA_MIN_TRAJECTORIES


@dataclass(frozen=True)
class EvalCase:
    """One scored trajectory we want the variant to replicate or beat.

    ``user_request`` is the prompt that opened the turn; ``rating`` is
    the Phase 44 classifier or explicit-feedback verdict (-1 / 0 / 1).
    ``reason`` is the free-text classifier rationale, used as a target
    signal for variants to surface.
    """

    turn_id: str
    user_request: str
    rating: int
    reason: str | None
    session_id: str


def mine_eval_set_from_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    min_cases: int = GEPA_MIN_TRAJECTORIES,
    max_cases: int = GEPA_MAX_TRAJECTORIES,
) -> list[EvalCase]:
    """Build an :class:`EvalCase` list from outcome dicts.

    Only ``rating != 0`` rows count toward the eval set (neutral turns
    carry no signal). Returns at most ``max_cases``; if fewer than
    ``min_cases`` informative rows are available the caller should
    skip the run rather than score on noise — :func:`run_gepa` checks
    this and aborts.
    """
    cases: list[EvalCase] = []
    for o in outcomes:
        rating = int(o.get("rating", 0))
        if rating == 0:
            continue
        request = str(o.get("user_request") or "").strip()
        if not request:
            continue
        cases.append(
            EvalCase(
                turn_id=str(o.get("turn_id") or ""),
                user_request=request,
                rating=rating,
                reason=o.get("reason"),
                session_id=str(o.get("session_id") or ""),
            )
        )
        if len(cases) >= max_cases:
            break
    del min_cases  # caller decides what to do with len(cases) < min_cases
    return cases


async def mine_eval_set(
    *,
    skill_slug: str,
    user_id: str,
    agent_id: str | None = None,
    min_cases: int = GEPA_MIN_TRAJECTORIES,
    max_cases: int = GEPA_MAX_TRAJECTORIES,
) -> list[EvalCase]:
    """Mine the eval set for one skill from Postgres ``turn_outcomes``.

    Returns a list of resolved :class:`EvalCase`s drawn from at most
    ``max_cases`` classified turns with a non-zero rating. The cloud
    path opens its own short-lived ``db_session`` per call so the
    function stays usable from the worker without sharing state.
    """
    # Imported lazily so the CLI path can use the local store path
    # without booting the async PG driver.
    from sqlalchemy import and_, desc, select  # noqa: PLC0415

    from lite_horse.models.message import Message  # noqa: PLC0415
    from lite_horse.models.turn_outcome import TurnOutcome  # noqa: PLC0415
    from lite_horse.storage.db import db_session  # noqa: PLC0415

    async with db_session(user_id, agent_id) as session:
        outcome_stmt = (
            select(
                TurnOutcome.turn_id,
                TurnOutcome.rating,
                TurnOutcome.reason,
                TurnOutcome.session_id,
            )
            .where(
                and_(
                    TurnOutcome.skill_slug == skill_slug,
                    TurnOutcome.rating != 0,
                )
            )
            .order_by(desc(TurnOutcome.ts))
            .limit(int(max_cases) * 2)
        )
        outcome_rows = (await session.execute(outcome_stmt)).all()
        raw: list[dict[str, Any]] = []
        for r in outcome_rows:
            msg_stmt = (
                select(Message.content)
                .where(
                    and_(
                        Message.session_id == r.session_id,
                        Message.role == "user",
                    )
                )
                .order_by(Message.ts.asc(), Message.id.asc())
                .limit(1)
            )
            user_text = (await session.execute(msg_stmt)).scalar_one_or_none()
            raw.append(
                {
                    "turn_id": str(r.turn_id),
                    "user_request": user_text or "",
                    "rating": int(r.rating),
                    "reason": r.reason,
                    "session_id": str(r.session_id),
                }
            )

    return mine_eval_set_from_outcomes(
        raw, min_cases=min_cases, max_cases=max_cases
    )
