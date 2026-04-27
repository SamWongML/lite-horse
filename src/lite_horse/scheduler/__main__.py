"""Scheduler service entrypoint — ``python -m lite_horse.scheduler``.

Boots an :class:`AsyncIOScheduler` with a single recurring job that
calls :func:`lite_horse.scheduler.tick.tick` every 60 s. The tick is
the unit of work; the persistent state lives in Postgres
(``cron_jobs.last_fired_at``) so a scheduler restart loses nothing —
the next tick re-derives due rows from PG.

The plan calls for a ``SQLAlchemyJobStore`` to back APScheduler. With a
single recurring tick that's effectively redundant — the only job in
the store is the tick itself, and it's re-registered on every boot.
We therefore keep APScheduler in-memory and rely on the PG-resident
``cron_jobs`` table for durability. Documented as the deviation; if
Phase 39 needs APScheduler-managed individual jobs (per-cron triggers
instead of a tick), swap to ``SQLAlchemyJobStore`` then.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from lite_horse.config import get_settings
from lite_horse.scheduler.tick import tick
from lite_horse.storage import make_message_queue
from lite_horse.storage.queue import MessageQueue

TICK_SECONDS = 60

log = logging.getLogger(__name__)


def _wrap_tick(queue: MessageQueue) -> Any:
    async def _job() -> None:
        try:
            await tick(queue=queue)
        except Exception:
            log.exception("scheduler tick failed")

    return _job


async def run_scheduler() -> None:
    """Async entrypoint: schedule the tick, wait for SIGINT/SIGTERM."""
    settings = get_settings()
    log.info("scheduler boot env=%s tick=%ds", settings.env, TICK_SECONDS)
    queue = make_message_queue()

    sched = AsyncIOScheduler()
    sched.add_job(
        _wrap_tick(queue),
        trigger=IntervalTrigger(seconds=TICK_SECONDS),
        id="cron-tick",
        replace_existing=True,
        next_run_time=None,  # first fire after one TICK_SECONDS
    )
    sched.start()
    log.info("scheduler up")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        await stop.wait()
    finally:
        sched.shutdown(wait=False)
        log.info("scheduler down")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
