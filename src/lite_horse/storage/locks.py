"""SessionLock Protocol — distributed mutual exclusion across ECS tasks.

Cloud impl (`locks_redis.py`) uses Redis SET NX PX. Local impl
(`locks_memory.py`) uses an in-process asyncio Lock — sufficient for
unit tests and single-process dev runs.

Usage:

    async with lock(key="session:abc", ttl=300):
        ...

A waiter that fails to acquire within `wait` seconds raises
`LockTimeout`. The TTL upper-bounds how long the lock can be held even
if the holder crashes.
"""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol, runtime_checkable


class LockTimeoutError(TimeoutError):
    """Raised when a SessionLock cannot be acquired within the wait budget."""


# Back-compat alias kept for clarity at call sites.
LockTimeout = LockTimeoutError


@runtime_checkable
class SessionLock(Protocol):
    """Factory: call with key+ttl to get an async context manager."""

    def __call__(
        self, key: str, ttl: float = 300.0, wait: float = 30.0
    ) -> AbstractAsyncContextManager[None]:
        ...
