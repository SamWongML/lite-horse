"""Phase 39 per-user rate limit on POST /v1/turns."""
from __future__ import annotations

import time

import pytest

from lite_horse.web.rate_limit import (
    DEFAULT_TURN_RATE_PER_MIN,
    check_and_consume,
)

pytestmark = pytest.mark.asyncio


class _FakeRedis:
    """Minimal asyncio-style redis stand-in supporting INCR + EXPIRE pipelines."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, store: _FakeRedis) -> None:
        self.store = store
        self.ops: list[tuple[str, str, int]] = []

    def incr(self, key: str) -> _FakePipeline:
        self.ops.append(("incr", key, 0))
        return self

    def expire(self, key: str, ttl: int) -> _FakePipeline:
        self.ops.append(("expire", key, ttl))
        return self

    async def execute(self) -> list[int]:
        results: list[int] = []
        for op, key, arg in self.ops:
            if op == "incr":
                self.store.values[key] = self.store.values.get(key, 0) + 1
                results.append(self.store.values[key])
            elif op == "expire":
                self.store.expirations[key] = arg
                results.append(1)
        return results


async def test_no_redis_short_circuits_to_allow() -> None:
    assert await check_and_consume(None, user_id="u1") is True


async def test_first_call_allowed_at_default_limit() -> None:
    redis = _FakeRedis()
    allowed = await check_and_consume(redis, user_id="u1", now=time.time())  # type: ignore[arg-type]
    assert allowed is True


async def test_blocks_after_quota_exhausted() -> None:
    redis = _FakeRedis()
    now = time.time()
    cap = 5
    for _ in range(cap):
        assert await check_and_consume(  # type: ignore[arg-type]
            redis, user_id="u1", limit_per_min=cap, now=now
        )
    over = await check_and_consume(  # type: ignore[arg-type]
        redis, user_id="u1", limit_per_min=cap, now=now
    )
    assert over is False


async def test_separate_users_dont_share_buckets() -> None:
    redis = _FakeRedis()
    now = time.time()
    cap = 1
    a_first = await check_and_consume(redis, user_id="u1", limit_per_min=cap, now=now)  # type: ignore[arg-type]
    b_first = await check_and_consume(redis, user_id="u2", limit_per_min=cap, now=now)  # type: ignore[arg-type]
    assert a_first is True
    assert b_first is True


async def test_window_rolls_over_after_60s() -> None:
    redis = _FakeRedis()
    cap = 1
    now = 1_700_000_000.0
    assert await check_and_consume(redis, user_id="u1", limit_per_min=cap, now=now)  # type: ignore[arg-type]
    assert not await check_and_consume(redis, user_id="u1", limit_per_min=cap, now=now)  # type: ignore[arg-type]
    # Next minute: fresh bucket key.
    assert await check_and_consume(  # type: ignore[arg-type]
        redis, user_id="u1", limit_per_min=cap, now=now + 60
    )


async def test_zero_limit_unlimited_tier() -> None:
    redis = _FakeRedis()
    now = time.time()
    for _ in range(DEFAULT_TURN_RATE_PER_MIN * 2):
        assert await check_and_consume(  # type: ignore[arg-type]
            redis, user_id="u1", limit_per_min=0, now=now
        )
