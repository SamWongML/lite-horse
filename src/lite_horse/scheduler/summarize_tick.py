"""Hourly summarize-enqueue tick.

Scans :meth:`SessionSummaryRepo.find_idle_sessions` cross-tenant, then
posts one :class:`SummarizeMessage` per idle session to the worker
queue. The worker dispatcher routes by ``kind`` so this re-uses the
same queue as the cron / evolve / embed paths.

The scheduler opens its own ``db_session`` with no tenant GUC, so the
``find_idle_sessions`` query bypasses RLS via the empty-string fallback
documented in :mod:`lite_horse.repositories.session_summary_repo`.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from lite_horse.observability import emit_metric
from lite_horse.repositories.session_summary_repo import SessionSummaryRepo
from lite_horse.storage.db import db_session
from lite_horse.storage.queue import MessageQueue
from lite_horse.worker.summarize import SummarizeMessage

log = logging.getLogger(__name__)

DEFAULT_IDLE_HOURS = 24
DEFAULT_SCAN_LIMIT = 200


async def summarize_tick(
    *,
    queue: MessageQueue,
    now: datetime | None = None,
    idle_hours: int = DEFAULT_IDLE_HOURS,
    limit: int = DEFAULT_SCAN_LIMIT,
) -> int:
    """Run one idle-session sweep. Returns the number of messages enqueued."""
    moment = now or datetime.now(UTC)
    async with db_session(user_id=None) as session:
        repo = SessionSummaryRepo(session)
        idle = await repo.find_idle_sessions(
            idle_hours=idle_hours, limit=limit, now=moment
        )
    enqueued = 0
    for s in idle:
        if s.agent_id is None:
            log.warning(
                "summarize tick: session %s has no agent_id; skipping",
                s.session_id,
            )
            continue
        msg = SummarizeMessage.new(
            user_id=s.user_id,
            agent_id=s.agent_id,
            session_id=s.session_id,
        )
        await queue.send(msg.to_json())
        enqueued += 1
    if enqueued:
        log.info("summarize tick enqueued %d summaries", enqueued)
        emit_metric("summarize_enqueued_total", enqueued)
    return enqueued
