"""Weekly GEPA-enqueue tick.

Scans every current user-scope skill whose ``frontmatter.gepa`` is
truthy and posts one :class:`EvolveGepaMessage` per ``(user, agent,
slug)``. The scheduler runs in admin context (no ``app.user_id``
GUC), so the scan uses an explicit ``WHERE`` on the JSONB column
rather than relying on RLS.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import and_, select

from lite_horse.models.skill import Skill
from lite_horse.observability import emit_metric
from lite_horse.storage.db import db_session
from lite_horse.storage.queue import MessageQueue
from lite_horse.worker.gepa import EvolveGepaMessage

log = logging.getLogger(__name__)


async def gepa_tick(
    *, queue: MessageQueue, now: datetime | None = None
) -> int:
    """One weekly GEPA sweep. Returns the number of messages enqueued."""
    moment = now or datetime.now(UTC)
    async with db_session(user_id=None) as session:
        stmt = (
            select(Skill.user_id, Skill.agent_id, Skill.slug)
            .where(
                and_(
                    Skill.scope == "user",
                    Skill.is_current.is_(True),
                    Skill.agent_id.is_not(None),
                    Skill.frontmatter["gepa"].astext == "true",
                )
            )
            .order_by(Skill.user_id, Skill.agent_id, Skill.slug)
        )
        rows = (await session.execute(stmt)).all()
    enqueued = 0
    for r in rows:
        msg = EvolveGepaMessage.new(
            user_id=str(r.user_id),
            agent_id=str(r.agent_id),
            skill_slug=str(r.slug),
            enqueued_at=moment.isoformat(),
        )
        await queue.send(msg.to_json())
        enqueued += 1
    if enqueued:
        log.info("gepa tick enqueued %d runs", enqueued)
        emit_metric("gepa_enqueued_total", enqueued)
    return enqueued
