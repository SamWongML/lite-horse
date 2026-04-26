"""In-memory SessionLock — local dev + unit tests."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from lite_horse.storage.locks import LockTimeoutError


class InMemorySessionLock:
    """Per-key asyncio Lock with TTL-based hold expiration.

    `wait` bounds how long an acquirer waits; `ttl` bounds how long a
    holder can keep the lock before it auto-expires (mirrors Redis SET PX).
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._holders: dict[str, asyncio.Task[None]] = {}
        self._registry_lock = asyncio.Lock()

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._registry_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    @asynccontextmanager
    async def _acquire(self, key: str, ttl: float, wait: float) -> AsyncIterator[None]:
        lock = await self._get_lock(key)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=wait)
        except TimeoutError as exc:
            raise LockTimeoutError(
                f"could not acquire {key!r} within {wait}s"
            ) from exc

        async def _expire() -> None:
            try:
                await asyncio.sleep(ttl)
            except asyncio.CancelledError:
                return
            if lock.locked():
                lock.release()

        expirer = asyncio.create_task(_expire())
        self._holders[key] = expirer
        try:
            yield
        finally:
            expirer.cancel()
            self._holders.pop(key, None)
            if lock.locked():
                lock.release()

    def __call__(
        self, key: str, ttl: float = 300.0, wait: float = 30.0
    ) -> AbstractAsyncContextManager[None]:
        return self._acquire(key, ttl, wait)
