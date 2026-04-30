"""Daily evolve enqueue tick — Phase 39.

Distinct from :mod:`lite_horse.scheduler.tick` (which scans
``cron_jobs`` once a minute). The evolve tick fires once a day, asks
:func:`lite_horse.evolve.cloud.find_evolve_candidates` for the
(user x skill) pairs whose trajectory count has crossed the threshold,
and posts one :class:`EvolveMessage` per candidate to the same queue
the worker drains.

It intentionally re-uses the existing SQS queue rather than introducing
a second queue: the worker dispatcher routes by the message body's
``kind`` field.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from lite_horse.evolve.cloud import EvolveMessage, find_evolve_candidates
from lite_horse.observability import emit_metric
from lite_horse.storage.queue import MessageQueue

log = logging.getLogger(__name__)


async def evolve_tick(
    *, queue: MessageQueue, now: datetime | None = None
) -> int:
    """Run one daily evolve sweep. Returns the number of messages enqueued."""
    moment = now or datetime.now(UTC)
    candidates = await find_evolve_candidates(now=moment)
    if not candidates:
        return 0
    enqueued = 0
    for candidate in candidates:
        msg = EvolveMessage.new(
            user_id=candidate.user_id,
            skill_slug=candidate.skill_slug,
            now=moment,
        )
        await queue.send(msg.to_json())
        enqueued += 1
    log.info("evolve tick enqueued %d evolve runs", enqueued)
    emit_metric("evolve_enqueued_total", enqueued)
    return enqueued
