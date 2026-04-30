"""Per-user daily cost budget on ``POST /v1/turns*``.

Redis counter ``cost:day:{user_id}:{YYYYMMDD}`` accumulates the
post-turn cost in micro-USD. Two checkpoints:

* **Pre-turn check** — if the counter is already at or above the
  per-user cap, the request is rejected with ``cost_budget_exceeded``
  before the agent runs.
* **Post-turn record** — after a successful turn the counter is
  incremented by that turn's ``cost_usd_micro``. An ``alerts_total``
  EMF metric fires the first time a user crosses 80% of the cap so
  ops can surface a banner without polling Postgres.

The counter expires 48 h after first write so a stale day's row
self-cleans without a sweeper.
"""
from __future__ import annotations

from datetime import UTC, datetime

from lite_horse.observability import emit_metric
from lite_horse.storage.redis_client import Redis

_PREFIX = "cost:day:"
_ALERT_PREFIX = "cost:alerted:"
_DAY_SECONDS = 86400
_TTL_SECONDS = _DAY_SECONDS * 2
ALERT_THRESHOLD_PCT = 80


def _ymd(now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return moment.strftime("%Y%m%d")


def _counter_key(user_id: str, ymd: str) -> str:
    return f"{_PREFIX}{user_id}:{ymd}"


def _alert_key(user_id: str, ymd: str) -> str:
    return f"{_ALERT_PREFIX}{user_id}:{ymd}"


async def get_spent_micro(
    redis: Redis | None, *, user_id: str, now: datetime | None = None
) -> int:
    """Return the user's micro-USD spend so far today (UTC), 0 on miss."""
    if redis is None:
        return 0
    raw = await redis.get(_counter_key(user_id, _ymd(now)))
    if raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


async def check_budget(
    redis: Redis | None,
    *,
    user_id: str,
    budget_micro: int | None,
    now: datetime | None = None,
) -> bool:
    """Return True if the user is still under their daily cap.

    ``budget_micro=None`` (no override) is treated as "no budget" and
    always returns True. Tests can pass ``redis=None`` to short-circuit.
    """
    if redis is None or budget_micro is None or budget_micro <= 0:
        return True
    spent = await get_spent_micro(redis, user_id=user_id, now=now)
    return spent < budget_micro


async def record_cost(
    redis: Redis | None,
    *,
    user_id: str,
    cost_micro: int,
    budget_micro: int | None = None,
    now: datetime | None = None,
) -> int:
    """Increment today's counter by ``cost_micro``; return the new total.

    When ``budget_micro`` is set and this write is the first to cross
    80% of the cap, emit a ``cost_budget_alert`` metric exactly once
    per user-day.
    """
    if redis is None or cost_micro <= 0:
        return 0
    ymd = _ymd(now)
    key = _counter_key(user_id, ymd)
    pipe = redis.pipeline()
    pipe.incrby(key, int(cost_micro))
    pipe.expire(key, _TTL_SECONDS)
    new_total, _ = await pipe.execute()
    total = int(new_total)
    if budget_micro and budget_micro > 0:
        threshold = budget_micro * ALERT_THRESHOLD_PCT // 100
        previous = total - int(cost_micro)
        if previous < threshold <= total:
            already = await redis.set(
                _alert_key(user_id, ymd), "1", ex=_TTL_SECONDS, nx=True
            )
            if already:
                emit_metric(
                    "cost_budget_alert",
                    1,
                    dimensions={"threshold_pct": str(ALERT_THRESHOLD_PCT)},
                )
    return total
