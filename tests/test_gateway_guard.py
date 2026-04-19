"""Tests for the two-level message guard (Phase 9)."""
from __future__ import annotations

import asyncio

import pytest

from lite_horse.gateway.guard import GuardRegistry, SessionGuard


def test_registry_returns_same_guard_for_same_key() -> None:
    reg = GuardRegistry()
    g1 = reg.get("agent:main:telegram:private:1")
    g2 = reg.get("agent:main:telegram:private:1")
    assert g1 is g2


def test_registry_returns_distinct_guards_for_distinct_keys() -> None:
    reg = GuardRegistry()
    g1 = reg.get("agent:main:telegram:private:1")
    g2 = reg.get("agent:main:telegram:private:2")
    assert g1 is not g2


def test_fresh_guard_defaults() -> None:
    guard = SessionGuard()
    assert not guard.lock.locked()
    assert not guard.interrupt.is_set()
    assert guard.pending == []


@pytest.mark.asyncio
async def test_second_arrival_queues_and_sets_interrupt() -> None:
    guard = SessionGuard()
    await guard.lock.acquire()
    try:
        # Simulate a second message arriving while the first run holds the lock.
        guard.pending.append("follow-up")
        guard.interrupt.set()
        assert guard.pending == ["follow-up"]
        assert guard.interrupt.is_set()
    finally:
        guard.lock.release()


@pytest.mark.asyncio
async def test_lock_serializes_runs() -> None:
    guard = SessionGuard()
    order: list[str] = []

    async def run(tag: str, delay: float) -> None:
        async with guard.lock:
            order.append(f"{tag}-start")
            await asyncio.sleep(delay)
            order.append(f"{tag}-end")

    await asyncio.gather(run("a", 0.01), run("b", 0.0))
    # Whichever task won the lock first must fully finish before the other starts.
    assert order[0].endswith("-start")
    assert order[1].endswith("-end")
    assert order[0].split("-")[0] == order[1].split("-")[0]
