"""Daily GDPR-delete tick — Phase 46.

Runs in admin context (no ``app.user_id`` GUC). Scans
:meth:`GdprDeleteRepo.list_due` for every request whose
``scheduled_at`` has passed, then enqueues one :class:`GdprDeleteMessage`
per request so the worker handles the export + purge with its own
crash recovery / DLQ.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from lite_horse.observability import emit_metric
from lite_horse.repositories.gdpr_delete_repo import GdprDeleteRepo
from lite_horse.storage.db import db_session
from lite_horse.storage.queue import MessageQueue
from lite_horse.worker.gdpr import GdprDeleteMessage

log = logging.getLogger(__name__)

DEFAULT_SCAN_LIMIT = 100


async def gdpr_tick(
    *,
    queue: MessageQueue,
    now: datetime | None = None,
    limit: int = DEFAULT_SCAN_LIMIT,
) -> int:
    """Enqueue one message per due delete request. Returns the count."""
    when = now or datetime.now(UTC)
    async with db_session(user_id=None) as session:
        repo = GdprDeleteRepo(session)
        due = await repo.list_due(now=when, limit=limit)
    enqueued = 0
    for row in due:
        msg = GdprDeleteMessage.new(request_id=str(row.id))
        await queue.send(msg.to_json())
        enqueued += 1
    if enqueued:
        log.info("gdpr tick enqueued %d deletes", enqueued)
        emit_metric("gdpr_deletes_enqueued_total", enqueued)
    return enqueued
