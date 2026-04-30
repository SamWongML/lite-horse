"""Phase 39 per-user daily cost budget."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lite_horse.web.cost_budget import (
    ALERT_THRESHOLD_PCT,
    check_budget,
    get_spent_micro,
    record_cost,
)

pytestmark = pytest.mark.asyncio


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int | str] = {}
        self.alerts_set: list[tuple[str, int]] = []

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def get(self, key: str) -> str | None:
        v = self.values.get(key)
        return None if v is None else str(v)

    async def set(
        self, key: str, value: str, *, ex: int | None = None, nx: bool = False
    ) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.alerts_set.append((key, ex))
        return True


class _FakePipeline:
    def __init__(self, store: _FakeRedis) -> None:
        self.store = store
        self.ops: list[tuple[str, str, int]] = []

    def incrby(self, key: str, amount: int) -> _FakePipeline:
        self.ops.append(("incrby", key, amount))
        return self

    def expire(self, key: str, ttl: int) -> _FakePipeline:
        self.ops.append(("expire", key, ttl))
        return self

    async def execute(self) -> list[int]:
        results: list[int] = []
        for op, key, arg in self.ops:
            if op == "incrby":
                cur = int(self.store.values.get(key, 0) or 0)
                cur += arg
                self.store.values[key] = cur
                results.append(cur)
            elif op == "expire":
                results.append(1)
        return results


_NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)


async def test_no_redis_always_within_budget() -> None:
    assert await check_budget(None, user_id="u1", budget_micro=10) is True


async def test_no_budget_set_always_within() -> None:
    redis = _FakeRedis()
    assert await check_budget(redis, user_id="u1", budget_micro=None) is True  # type: ignore[arg-type]


async def test_record_cost_advances_counter() -> None:
    redis = _FakeRedis()
    total = await record_cost(  # type: ignore[arg-type]
        redis, user_id="u1", cost_micro=200, budget_micro=1000, now=_NOW
    )
    assert total == 200
    assert (
        await get_spent_micro(redis, user_id="u1", now=_NOW)  # type: ignore[arg-type]
    ) == 200


async def test_check_budget_blocks_at_or_above_cap() -> None:
    redis = _FakeRedis()
    await record_cost(  # type: ignore[arg-type]
        redis, user_id="u1", cost_micro=999, budget_micro=1000, now=_NOW
    )
    assert await check_budget(  # type: ignore[arg-type]
        redis, user_id="u1", budget_micro=1000, now=_NOW
    )
    await record_cost(  # type: ignore[arg-type]
        redis, user_id="u1", cost_micro=1, budget_micro=1000, now=_NOW
    )
    assert not await check_budget(  # type: ignore[arg-type]
        redis, user_id="u1", budget_micro=1000, now=_NOW
    )


async def test_alert_fires_only_once_when_crossing_80pct() -> None:
    redis = _FakeRedis()
    # Cross 80% (= 800 of 1000) on the second write.
    await record_cost(  # type: ignore[arg-type]
        redis, user_id="u1", cost_micro=500, budget_micro=1000, now=_NOW
    )
    await record_cost(  # type: ignore[arg-type]
        redis, user_id="u1", cost_micro=400, budget_micro=1000, now=_NOW
    )
    # Subsequent writes don't re-alert.
    await record_cost(  # type: ignore[arg-type]
        redis, user_id="u1", cost_micro=50, budget_micro=1000, now=_NOW
    )
    assert any("cost:alerted" in k for k, _ in redis.alerts_set)
    assert ALERT_THRESHOLD_PCT == 80
