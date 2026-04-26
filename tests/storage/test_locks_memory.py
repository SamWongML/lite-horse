"""InMemorySessionLock contract."""
from __future__ import annotations

import asyncio

import pytest

from lite_horse.storage.locks import LockTimeout, SessionLock
from lite_horse.storage.locks_memory import InMemorySessionLock


@pytest.fixture()
def lock() -> InMemorySessionLock:
    return InMemorySessionLock()


def test_satisfies_protocol(lock: InMemorySessionLock) -> None:
    assert isinstance(lock, SessionLock)


async def test_serialises_concurrent_holders(lock: InMemorySessionLock) -> None:
    order: list[str] = []

    async def task(name: str, hold: float) -> None:
        async with lock("session:k", ttl=10, wait=5):
            order.append(f"{name}:enter")
            await asyncio.sleep(hold)
            order.append(f"{name}:exit")

    await asyncio.gather(task("a", 0.05), task("b", 0.05))
    assert order in (
        ["a:enter", "a:exit", "b:enter", "b:exit"],
        ["b:enter", "b:exit", "a:enter", "a:exit"],
    )


async def test_distinct_keys_do_not_block(lock: InMemorySessionLock) -> None:
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def hold_a() -> None:
        async with lock("a", ttl=10):
            started.set()
            await proceed.wait()

    async def use_b() -> bool:
        await started.wait()
        async with lock("b", ttl=10, wait=0.5):
            return True

    a = asyncio.create_task(hold_a())
    b = asyncio.create_task(use_b())
    assert await asyncio.wait_for(b, timeout=2)
    proceed.set()
    await a


async def test_wait_timeout_raises(lock: InMemorySessionLock) -> None:
    proceed = asyncio.Event()

    async def hold() -> None:
        async with lock("contended", ttl=10):
            await proceed.wait()

    held = asyncio.create_task(hold())
    await asyncio.sleep(0.01)
    with pytest.raises(LockTimeout):
        async with lock("contended", ttl=10, wait=0.05):
            pass
    proceed.set()
    await held


async def test_ttl_releases_lock(lock: InMemorySessionLock) -> None:
    async def hold_forever() -> None:
        async with lock("ttl-key", ttl=0.05, wait=1):
            await asyncio.sleep(1)

    holder = asyncio.create_task(hold_forever())
    await asyncio.sleep(0.1)  # ttl expires
    async with lock("ttl-key", ttl=10, wait=0.5):
        pass
    holder.cancel()
    try:
        await holder
    except asyncio.CancelledError:
        pass
