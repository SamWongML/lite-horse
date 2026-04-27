"""Ask-mode permission broker for ``/v1/turns/{turn_id}/decisions``.

When a turn is started in ``permission_mode='ask'`` and the agent reaches
a write tool, the streaming generator publishes a
``permission_request`` SSE event and parks on a future. The decisions
endpoint resolves the future on either ECS task — local resolution is
direct; cross-task resolution rides Redis pub/sub on
``permission-decisions``.

Wire shape (kept intentionally small so the agent-side hook can plug in):

* ``await broker.request_decision(turn_id, tool_call_id, ...)`` ->
  ``"allow" | "deny"``. The caller (the agent's tool wrapper) blocks
  until a decision arrives.
* ``await broker.resolve(turn_id, tool_call_id, decision)`` is invoked
  by the decisions HTTP handler. If the awaiter lives in this process
  the future fires synchronously; otherwise the message is forwarded
  via Redis pub/sub to whichever process owns the future.
* The streaming generator drains pending requests via
  ``broker.pending_events(turn_id)`` so it can emit
  ``permission_request`` SSE events as they appear.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from lite_horse.storage.redis_client import Redis

CHANNEL = "permission-decisions"
_VALID_DECISIONS = frozenset({"allow", "deny"})

_log = logging.getLogger(__name__)


@dataclass
class PermissionRequest:
    """One pending tool-call decision."""

    tool_call_id: str
    tool: str
    args: Any
    future: asyncio.Future[str] = field(default_factory=asyncio.Future)


@dataclass
class _TurnState:
    """Per-turn registry entry."""

    requests: dict[str, PermissionRequest] = field(default_factory=dict)
    queue: asyncio.Queue[PermissionRequest] = field(default_factory=asyncio.Queue)


class PermissionBroker:
    """Process-local broker; optional Redis pub/sub for cross-task delivery."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._turns: dict[str, _TurnState] = {}
        self._redis = redis
        self._sub_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    # ----- lifecycle -----

    async def start(self) -> None:
        """Spawn the cross-task subscriber, if a Redis client is wired."""
        if self._redis is None or self._sub_task is not None:
            return
        self._stop_event = asyncio.Event()
        self._sub_task = asyncio.create_task(self._run_subscriber())

    async def stop(self) -> None:
        if self._sub_task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        self._sub_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._sub_task
        self._sub_task = None
        self._stop_event = None

    # ----- agent-side surface -----

    async def request_decision(
        self,
        turn_id: str,
        tool_call_id: str,
        *,
        tool: str,
        args: Any,
    ) -> str:
        """Block until the user (or another task) decides on this tool call."""
        state = self._turns.setdefault(turn_id, _TurnState())
        if tool_call_id in state.requests:
            return await state.requests[tool_call_id].future
        loop = asyncio.get_running_loop()
        req = PermissionRequest(
            tool_call_id=tool_call_id,
            tool=tool,
            args=args,
            future=loop.create_future(),
        )
        state.requests[tool_call_id] = req
        await state.queue.put(req)
        try:
            return await req.future
        finally:
            state.requests.pop(tool_call_id, None)

    async def pending_events(self, turn_id: str) -> PermissionRequest:
        """Wait for the next pending permission request for ``turn_id``."""
        state = self._turns.setdefault(turn_id, _TurnState())
        return await state.queue.get()

    # ----- HTTP-side surface -----

    async def resolve(
        self, turn_id: str, tool_call_id: str, decision: str
    ) -> bool:
        """Resolve a pending request. Returns ``True`` if delivered locally."""
        if decision not in _VALID_DECISIONS:
            raise ValueError(f"invalid decision: {decision!r}")
        delivered = self._resolve_local(turn_id, tool_call_id, decision)
        if not delivered and self._redis is not None:
            payload = json.dumps(
                {
                    "turn_id": turn_id,
                    "tool_call_id": tool_call_id,
                    "decision": decision,
                }
            )
            await self._redis.publish(CHANNEL, payload)
        return delivered

    def _resolve_local(
        self, turn_id: str, tool_call_id: str, decision: str
    ) -> bool:
        state = self._turns.get(turn_id)
        if state is None:
            return False
        req = state.requests.get(tool_call_id)
        if req is None or req.future.done():
            return False
        req.future.set_result(decision)
        return True

    def cancel_turn(self, turn_id: str) -> None:
        """Fail any pending requests for ``turn_id`` so the awaiters unblock."""
        state = self._turns.pop(turn_id, None)
        if state is None:
            return
        for req in state.requests.values():
            if not req.future.done():
                req.future.set_exception(asyncio.CancelledError())

    # ----- pub/sub subscriber -----

    async def _run_subscriber(self) -> None:
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL)
        try:
            while True:
                if self._stop_event is not None and self._stop_event.is_set():
                    return
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg is None:
                    continue
                try:
                    data = json.loads(msg["data"])
                    self._resolve_local(
                        str(data["turn_id"]),
                        str(data["tool_call_id"]),
                        str(data["decision"]),
                    )
                except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                    _log.warning("permission-decision dropped: %r", msg)
        except asyncio.CancelledError:
            raise
        finally:
            try:
                await pubsub.unsubscribe(CHANNEL)
            finally:
                await pubsub.aclose()  # type: ignore[no-untyped-call]
