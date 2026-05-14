"""Daily skill-promotion candidate tick.

Runs in admin context (no ``app.user_id`` GUC). Asks
:meth:`SkillPromotionRepo.aggregate_candidates` for every
``frontmatter.name`` whose user-scope rows have crossed the promotion
thresholds, then upserts one pending row per name into
``skill_promotion_candidates``.

Unlike the curator tick, this tick does **not** enqueue worker
messages — the admin reviews and acts on the surface directly via the
``/v1/admin/skill-candidates`` routes.
"""
from __future__ import annotations

import logging
from datetime import datetime

from lite_horse.constants import (
    PROMOTION_MIN_SUCCESS_RATE,
    PROMOTION_MIN_UNIQUE_USERS,
    PROMOTION_MIN_USE_COUNT,
)
from lite_horse.observability import emit_metric
from lite_horse.repositories.skill_promotion_repo import SkillPromotionRepo
from lite_horse.storage.db import db_session

log = logging.getLogger(__name__)


async def promotion_tick(
    *,
    now: datetime | None = None,
    min_unique_users: int = PROMOTION_MIN_UNIQUE_USERS,
    min_use_count: int = PROMOTION_MIN_USE_COUNT,
    min_success_rate: float = PROMOTION_MIN_SUCCESS_RATE,
) -> int:
    """One promotion sweep. Returns the number of candidates upserted."""
    del now  # signature parity with sibling ticks
    async with db_session(user_id=None) as session:
        repo = SkillPromotionRepo(session)
        aggregates = await repo.aggregate_candidates(
            min_unique_users=min_unique_users,
            min_use_count=min_use_count,
            min_success_rate=min_success_rate,
        )
        for agg in aggregates:
            await repo.upsert_pending(agg)
    count = len(aggregates)
    if count:
        log.info("promotion tick upserted %d candidates", count)
        emit_metric("promotion_candidates_total", count)
    return count
