"""Daily curator-enqueue tick.

Scans :meth:`SkillRepo.list_curator_targets_admin` for every
``(user_id, agent_id)`` slice that owns at least one user-scope skill,
and posts one :class:`CurateMessage` per slice to the worker queue.
The actual state transitions + consolidation proposals happen inside
the worker so the tick stays a thin enumerator.
"""
from __future__ import annotations

import logging
from datetime import datetime

from lite_horse.observability import emit_metric
from lite_horse.repositories.skill_repo import SkillRepo
from lite_horse.storage.db import db_session
from lite_horse.storage.queue import MessageQueue
from lite_horse.worker.curate import CurateMessage

log = logging.getLogger(__name__)

DEFAULT_SCAN_LIMIT = 500


async def curator_tick(
    *,
    queue: MessageQueue,
    now: datetime | None = None,
    limit: int = DEFAULT_SCAN_LIMIT,
) -> int:
    """Run one curator sweep. Returns the number of messages enqueued."""
    del now  # signature parity with sibling ticks
    async with db_session(user_id=None) as session:
        repo = SkillRepo(session)
        slices = await repo.list_curator_targets_admin(limit=limit)
    enqueued = 0
    for user_id, agent_id in slices:
        msg = CurateMessage.new(user_id=user_id, agent_id=agent_id)
        await queue.send(msg.to_json())
        enqueued += 1
    if enqueued:
        log.info("curator tick enqueued %d passes", enqueued)
        emit_metric("curator_enqueued_total", enqueued)
    return enqueued
