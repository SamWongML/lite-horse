"""Redis cache wrapper around :func:`compute_effective_config`.

Per the v0.4 Hard Contract, ``effective-config`` is computed per turn and
cached in Redis with a 60 s TTL. Phase 34 wires pub/sub invalidation on
admin writes; until then the TTL alone bounds staleness.

Cache layout:

* ``effective:{user_id}`` → JSON serialised :class:`EffectiveConfig`,
  TTL ``EFFECTIVE_CONFIG_TTL_SECONDS`` (60 s).

Etag is in the cached payload, so a follow-up HTTP route can compare with
``If-None-Match`` and return 304 without redeserialising. The user-id is
the cache key (not the etag) so a stale Redis entry self-evicts after
60 s without us needing to maintain a current-pointer indirection.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.effective import EffectiveConfig
from lite_horse.storage.redis_client import Redis
from lite_horse.web.effective_config import compute_effective_config

EFFECTIVE_CONFIG_TTL_SECONDS = 60


def _cache_key(user_id: str) -> str:
    return f"effective:{user_id}"


async def get_or_compute_effective_config(
    session: AsyncSession,
    *,
    redis: Redis | None,
    user_id: str,
) -> EffectiveConfig:
    """Return the cached config, recomputing on miss.

    ``redis=None`` short-circuits the cache — useful for tests that only
    want to exercise the resolver. The miss path always writes the
    computed value back, so a follow-up call within 60 s hits the cache.
    """
    if redis is not None:
        cached = await redis.get(_cache_key(user_id))
        if cached is not None:
            return EffectiveConfig.from_json(cached)

    eff = await compute_effective_config(session)

    if redis is not None:
        await redis.setex(
            _cache_key(user_id), EFFECTIVE_CONFIG_TTL_SECONDS, eff.to_json()
        )

    return eff


async def invalidate_effective_config(redis: Redis, user_id: str) -> None:
    """Drop the cache entry for ``user_id`` — used by Phase 34 pub/sub."""
    await redis.delete(_cache_key(user_id))
