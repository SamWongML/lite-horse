"""Per-tenant MCP connection pool — Phase 39.

The v0.3 path warmed a process-global ``_INIT_LOCK`` once at startup
and shared one set of ``MCPServer`` instances across every turn. Under
multi-tenant load that's wrong: each user resolves their own MCP set
(official + user-scope, with their own auth tokens), so the connection
state must be keyed on ``(user_id, server_url)``.

This module owns a fixed-size, TTL'd cache of already-``connect()``'d
``MCPServer`` instances. The turn driver calls :meth:`McpPool.acquire`
with a resolved :class:`EffectiveConfig` and gets back a list of live
servers ready to attach to ``Agent``. On TTL expiry or LRU eviction
the pool calls ``cleanup()`` so we don't leak HTTP connections.

Constraints (from the v0.4 plan):

* TTL 10 min — long enough that bursty conversations re-use a warm
  socket, short enough that a rotated MCP token starts working
  inside one TTL window without an explicit invalidate.
* Max 100 entries per task -- at 100 unique users x ~1 MCP each, the
  per-task footprint stays bounded; ECS scales horizontally.
* No cross-tenant leakage: the cache key is the **opaque** combination
  of user id and server URL, never just the URL.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agents.mcp import MCPServer, MCPServerStreamableHttp

from lite_horse.effective import EffectiveConfig, ResolvedMcpServer
from lite_horse.storage.kms import Kms

log = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 600.0
DEFAULT_MAX_ENTRIES = 100


@dataclass
class _Entry:
    server: MCPServer
    expires_at: float


def _cache_key(user_id: str, entry: ResolvedMcpServer) -> tuple[str, str, str]:
    """Tuple of (tenant key, slug, url) — slug change re-keys.

    ``user_id`` is the request tenant. ``entry.user_id`` is the entity's
    owning scope (None for official). We mix both so an official-scope
    server cached against user A still gets a fresh entry when user B
    asks for it — they may resolve different headers via their KMS
    decrypt context.
    """
    return (user_id, entry.slug, entry.url)


async def _build_server(
    *, user_id: str, entry: ResolvedMcpServer, kms: Kms
) -> MCPServer:
    """Decrypt the entry's auth value (if any) and connect the server."""
    params: dict[str, Any] = {"url": entry.url}
    if entry.auth_value_ct is not None and entry.auth_header:
        owner = entry.user_id or "official"
        plaintext = await kms.decrypt(entry.auth_value_ct, {"user_id": owner})
        params["headers"] = {entry.auth_header: plaintext.decode("utf-8")}
    server = MCPServerStreamableHttp(
        name=entry.slug,
        params=params,  # type: ignore[arg-type]
        cache_tools_list=entry.cache_tools_list,
    )
    await server.connect()  # type: ignore[no-untyped-call]
    return server


class McpPool:
    """Bounded async LRU cache of connected MCP servers."""

    def __init__(
        self,
        *,
        kms: Kms,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Any = time.monotonic,
    ) -> None:
        self._kms = kms
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._lock = asyncio.Lock()
        self._cache: OrderedDict[tuple[str, str, str], _Entry] = OrderedDict()

    def __len__(self) -> int:
        return len(self._cache)

    async def acquire(
        self, *, user_id: str, eff: EffectiveConfig
    ) -> list[MCPServer]:
        """Return one live ``MCPServer`` per entry in ``eff.mcp_servers``.

        Servers come straight from the cache when present and fresh; a
        miss decrypts the auth value, builds + connects a fresh server,
        and stores it under the LRU bound.
        """
        out: list[MCPServer] = []
        for entry in eff.mcp_servers:
            server = await self._get_or_build(user_id=user_id, entry=entry)
            out.append(server)
        return out

    async def _get_or_build(
        self, *, user_id: str, entry: ResolvedMcpServer
    ) -> MCPServer:
        key = _cache_key(user_id, entry)
        now = self._clock()
        async with self._lock:
            existing = self._cache.get(key)
            if existing is not None and existing.expires_at > now:
                self._cache.move_to_end(key)
                return existing.server
            if existing is not None:
                # Stale — drop and rebuild outside the lock.
                self._cache.pop(key, None)
                await _safe_cleanup(existing.server)
        server = await _build_server(user_id=user_id, entry=entry, kms=self._kms)
        async with self._lock:
            self._cache[key] = _Entry(server=server, expires_at=now + self._ttl)
            self._cache.move_to_end(key)
            await self._evict_if_full()
        return server

    async def _evict_if_full(self) -> None:
        while len(self._cache) > self._max:
            _, evicted = self._cache.popitem(last=False)
            await _safe_cleanup(evicted.server)

    async def invalidate(
        self, *, user_id: str | None = None, slug: str | None = None
    ) -> int:
        """Drop matching entries and disconnect them. Returns count evicted.

        Call sites: admin writes that change an MCP entry, or token
        rotation paths that need the next turn to re-decrypt.
        """
        async with self._lock:
            victims = [
                key
                for key in self._cache
                if (user_id is None or key[0] == user_id)
                and (slug is None or key[1] == slug)
            ]
            servers = [self._cache.pop(key).server for key in victims]
        for server in servers:
            await _safe_cleanup(server)
        return len(servers)

    async def shutdown(self) -> None:
        """Tear down every cached server. Idempotent."""
        async with self._lock:
            servers = [entry.server for entry in self._cache.values()]
            self._cache.clear()
        for server in servers:
            await _safe_cleanup(server)

    def keys(self) -> Iterable[tuple[str, str, str]]:
        return tuple(self._cache.keys())


async def _safe_cleanup(server: MCPServer) -> None:
    with contextlib.suppress(Exception):
        await server.cleanup()  # type: ignore[no-untyped-call]
