"""Daily compact-enqueue tick — Phase 43.

Scans :meth:`MemoryRepo.find_compaction_candidates` for ``(user, agent)``
pairs whose ``memory.md`` has crossed 80% utilisation, and posts one
:class:`CompactMessage` per candidate to the worker queue.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from lite_horse.observability import emit_metric
from lite_horse.repositories.memory_repo import MemoryRepo
from lite_horse.storage.db import db_session
from lite_horse.storage.queue import MessageQueue
from lite_horse.worker.compact import (
    COMPACT_UTILIZATION_THRESHOLD,
    CompactMessage,
)

log = logging.getLogger(__name__)

DEFAULT_SCAN_LIMIT = 200


async def compact_tick(
    *,
    queue: MessageQueue,
    now: datetime | None = None,
    threshold: float = COMPACT_UTILIZATION_THRESHOLD,
    limit: int = DEFAULT_SCAN_LIMIT,
) -> int:
    """Run one compaction sweep. Returns the number of messages enqueued."""
    del now  # signature parity with sibling ticks
    async with db_session(user_id=None) as session:
        repo = MemoryRepo(session)
        candidates = await repo.find_compaction_candidates(
            threshold=threshold, limit=limit
        )
    enqueued = 0
    for c in candidates:
        if c.agent_id is None:
            log.warning(
                "compact tick: user %s memory.md row has no agent_id; skipping",
                c.user_id,
            )
            continue
        msg = CompactMessage.new(user_id=c.user_id, agent_id=c.agent_id)
        await queue.send(msg.to_json())
        enqueued += 1
    if enqueued:
        log.info("compact tick enqueued %d compactions", enqueued)
        emit_metric("compact_enqueued_total", enqueued)
    return enqueued
