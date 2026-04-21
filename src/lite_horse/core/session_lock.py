"""Per-session-key ``asyncio.Lock`` registry.

``lite_horse.api.run_turn`` holds the lock for a key while a run is in flight;
two calls with the same key serialize, distinct keys run in parallel. Ported
from the deleted ``gateway/guard.py`` with the interrupt queue stripped — the
webapp handles queueing upstream.
"""
from __future__ import annotations

import asyncio


class SessionLockRegistry:
    """Lazily-populated ``session_key -> asyncio.Lock`` map."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock
