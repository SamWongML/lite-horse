"""Cloud :class:`CronBackend` impl — wraps :class:`CronRepo`.

Cron jobs in the cloud are slug-keyed (the agent passes ``job_id`` in the
tool call; here that string is interpreted as the slug). For ``add`` we
auto-generate a slug from the prompt + a short random suffix so two jobs
with the same prompt don't collide.

Webhook URLs land directly on ``cron_jobs.webhook_url``; the
``{platform: 'log'}`` delivery is mapped to ``webhook_url=None`` since
local-stdout delivery doesn't exist on the cloud worker. Round-tripping a
``log``-delivery row (by listing it) reports it back as ``log``.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from lite_horse.agent.backends.cron import CronBackend, CronJobView
from lite_horse.models.cron_job import CronJob
from lite_horse.repositories.cron_repo import CronRepo
from lite_horse.storage.db import db_session

_SLUG_SAFE = re.compile(r"[^a-z0-9]+")


def _slug_from_prompt(prompt: str) -> str:
    base = _SLUG_SAFE.sub("-", prompt.lower()).strip("-")[:32] or "job"
    suffix = uuid.uuid4().hex[:6]
    return f"{base}-{suffix}"


def _row_to_view(row: CronJob) -> CronJobView:
    delivery: dict[str, Any]
    if row.webhook_url:
        delivery = {"platform": "webhook", "url": row.webhook_url}
    else:
        delivery = {"platform": "log"}
    return CronJobView(
        id=row.slug,
        schedule=row.cron_expr,
        prompt=row.prompt,
        delivery=delivery,
        enabled=bool(row.enabled),
        disabled_reason=None,
    )


class CronCloudBackend(CronBackend):
    """Postgres-backed cron jobs scoped to one user (user-scope rows only)."""

    def __init__(self, *, user_id: str) -> None:
        self.user_id = user_id

    async def list_jobs(self) -> list[CronJobView]:
        async with db_session(self.user_id) as session:
            rows = await CronRepo(session).list_user()
        return [_row_to_view(r) for r in rows]

    async def add(
        self, *, schedule: str, prompt: str, delivery: dict[str, Any]
    ) -> CronJobView:
        slug = _slug_from_prompt(prompt)
        webhook_url = (
            delivery.get("url") if delivery.get("platform") == "webhook" else None
        )
        async with db_session(self.user_id) as session:
            row = await CronRepo(session).create_user(
                slug=slug,
                cron_expr=schedule,
                prompt=prompt,
                webhook_url=webhook_url,
            )
        return _row_to_view(row)

    async def remove(self, job_id: str) -> bool:
        async with db_session(self.user_id) as session:
            return await CronRepo(session).delete_user(job_id)

    async def set_enabled(self, job_id: str, *, enabled: bool) -> bool:
        async with db_session(self.user_id) as session:
            row = await CronRepo(session).update_user(job_id, enabled=enabled)
        return row is not None
