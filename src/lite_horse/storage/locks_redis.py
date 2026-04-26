"""Redis-backed `SessionLock` — `SET key token NX PX <ttl>` + Lua release.

The Lua script guarantees we only release a key we still own; if our TTL
already expired and another holder took the key, our DEL is a no-op.
"""
from __future__ import annotations

import asyncio
import secrets as _secrets
from collections.abc import AsyncIterator, Awaitable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, cast

from redis.asyncio import Redis

from lite_horse.storage.locks import LockTimeoutError
from lite_horse.storage.redis_client import make_redis_client

_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class RedisSessionLock:
    def __init__(
        self,
        client: Redis | None = None,
        retry_delay_seconds: float = 0.05,
    ) -> None:
        self._client = client if client is not None else make_redis_client()
        self._retry_delay = retry_delay_seconds

    @asynccontextmanager
    async def _acquire(self, key: str, ttl: float, wait: float) -> AsyncIterator[None]:
        token = _secrets.token_hex(16)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        ttl_ms = max(1, int(ttl * 1000))
        acquired = False
        while loop.time() < deadline:
            ok = await self._client.set(key, token, nx=True, px=ttl_ms)
            if ok:
                acquired = True
                break
            await asyncio.sleep(self._retry_delay)
        if not acquired:
            raise LockTimeoutError(f"could not acquire {key!r} within {wait}s")
        try:
            yield
        finally:
            # Lock auto-expires via Redis PX; release best-effort and swallow.
            try:
                # redis-py async stubs widen `eval`'s return to a sync/async
                # union; cast to the awaitable shape the runtime actually
                # produces.
                await cast(
                    Awaitable[Any], self._client.eval(_RELEASE_LUA, 1, key, token)
                )
            except Exception:
                pass

    def __call__(
        self, key: str, ttl: float = 300.0, wait: float = 30.0
    ) -> AbstractAsyncContextManager[None]:
        return self._acquire(key, ttl, wait)
