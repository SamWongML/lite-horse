"""Per-user rate limit on ``POST /v1/turns*``.

Redis-backed fixed-window counter under ``rate:turn:{user_id}:{epoch_min}``.
60 turns/min default, overridable per user via ``users.rate_limit_per_min``
(NULL → use default). Exhaustion raises a ``RATE_LIMIT`` error.

Fixed-window — not a strict token bucket — because:

* it's exact under contention (single Redis ``INCR``, no read-modify-write),
* the carry-over between minutes is bounded by 1x window size, which the
  60/min default tolerates fine, and
* it's one round-trip per request.

Tests can disable enforcement by passing ``redis=None``.
"""
from __future__ import annotations

import time

from lite_horse.storage.redis_client import Redis

DEFAULT_TURN_RATE_PER_MIN = 60
WINDOW_SECONDS = 60
_PREFIX = "rate:turn:"


def _key(user_id: str, *, now: float) -> str:
    bucket = int(now // WINDOW_SECONDS)
    return f"{_PREFIX}{user_id}:{bucket}"


async def check_and_consume(
    redis: Redis | None,
    *,
    user_id: str,
    limit_per_min: int | None = None,
    now: float | None = None,
) -> bool:
    """Increment the current 60-s bucket; return True if still under the cap.

    A NULL ``limit_per_min`` falls back to ``DEFAULT_TURN_RATE_PER_MIN``;
    a non-positive override disables the limit (unlimited tier). Without
    Redis (tests / local dev with disabled cache) the call is a no-op
    success.
    """
    if redis is None:
        return True
    cap = DEFAULT_TURN_RATE_PER_MIN if limit_per_min is None else int(limit_per_min)
    if cap <= 0:
        return True
    moment = time.time() if now is None else now
    key = _key(user_id, now=moment)
    pipe = redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, WINDOW_SECONDS * 2)
    count, _ = await pipe.execute()
    return int(count) <= cap
