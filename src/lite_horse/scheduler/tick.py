"""Pure tick logic — scan + fan-out + mark-fired.

Decoupled from the APScheduler harness so unit tests can run a single
``tick()`` against a live Postgres + an :class:`InMemoryMessageQueue`
without standing up the service entrypoint.

The tick opens its own :func:`db_session` with no tenant GUC because
the scheduler is a cross-tenant admin process. Cron rows have no RLS
(see DDL) so this is safe.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from lite_horse.cron.scheduler import (
    CronMessage,
    build_user_message,
    expand_official_to_user_messages,
    is_due,
)
from lite_horse.repositories.cron_repo import CronRepo
from lite_horse.repositories.user_repo import UserRepo
from lite_horse.storage.db import db_session
from lite_horse.storage.queue import MessageQueue

log = logging.getLogger(__name__)


async def tick(
    *, queue: MessageQueue, now: datetime | None = None
) -> int:
    """Run one scheduler pass. Returns the number of messages enqueued.

    Idempotent w.r.t. the same wall-clock minute — :func:`is_due`
    compares ``last_fired_at`` against the cron expression's next-fire
    boundary, so re-running a tick mid-minute won't double-fire.
    """
    moment = now or datetime.now(UTC)
    enqueued = 0
    async with db_session(user_id=None) as session:
        cron_repo = CronRepo(session)
        user_repo = UserRepo(session)
        active_user_ids = await user_repo.list_active_user_ids()
        rows = await cron_repo.list_enabled_admin()
        for row in rows:
            try:
                due = is_due(
                    cron_expr=row.cron_expr,
                    last_fired_at=row.last_fired_at,
                    now=moment,
                )
            except ValueError as exc:
                log.error(
                    "scheduler: invalid cron_expr on %s/%s: %s",
                    row.scope,
                    row.slug,
                    exc,
                )
                continue
            if not due:
                continue
            messages: list[CronMessage]
            if row.scope == "user":
                if row.user_id is None:
                    log.error(
                        "scheduler: user-scope job %s missing user_id; skipping",
                        row.slug,
                    )
                    continue
                messages = [
                    build_user_message(
                        cron_job_id=str(row.id),
                        slug=row.slug,
                        user_id=str(row.user_id),
                        prompt=row.prompt,
                        webhook_url=row.webhook_url,
                        scheduled_for=moment.isoformat(),
                    )
                ]
            else:
                messages = expand_official_to_user_messages(
                    cron_job_id=str(row.id),
                    slug=row.slug,
                    prompt=row.prompt,
                    webhook_url=row.webhook_url,
                    active_user_ids=active_user_ids,
                    scheduled_for=moment.isoformat(),
                )
            for msg in messages:
                await queue.send(msg.to_json())
                enqueued += 1
            await cron_repo.mark_fired_admin(row.id, when=moment)
    if enqueued:
        log.info("scheduler tick enqueued %d messages", enqueued)
    return enqueued
