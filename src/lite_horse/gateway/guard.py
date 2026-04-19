"""Two-level message guard for gateway sessions.

Each :class:`SessionGuard` owns an ``asyncio.Lock`` (level 1 — a run is in
flight) plus a pending-message queue with an interrupt event (level 2 — the
run can see queued follow-ups once it lands in the dispatcher again). The
:class:`GuardRegistry` hands out one guard per session key, created lazily.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class SessionGuard:
    """State for one session key. Held by the gateway runner."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    interrupt: asyncio.Event = field(default_factory=asyncio.Event)
    pending: list[str] = field(default_factory=list)


class GuardRegistry:
    """Lazily-populated map from session key to :class:`SessionGuard`."""

    def __init__(self) -> None:
        self._guards: dict[str, SessionGuard] = {}

    def get(self, key: str) -> SessionGuard:
        guard = self._guards.get(key)
        if guard is None:
            guard = SessionGuard()
            self._guards[key] = guard
        return guard
