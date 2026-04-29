"""Turn driver — orchestrates the agent stream behind the SSE/JSON routes.

Responsibilities:

1. Register an in-flight turn under a generated ``turn_id`` so
   ``/v1/turns/{turn_id}:abort`` can cancel it.
2. Translate :mod:`lite_horse.api` stream events into SSE bytes.
3. Drain the :class:`PermissionBroker` queue concurrently so
   ``permission_request`` events reach the SSE stream as they arise.
4. Surface a non-streaming runner that drains the same generator and
   returns the final text as JSON.

The actual stream source is provided via :class:`TurnRunner` so tests
can inject a deterministic event sequence without booting the agent
runtime. The default runner wraps :func:`lite_horse.api.run_turn_streaming`.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from lite_horse.observability import bind_log_context, emit_metric, get_logger
from lite_horse.providers.pricing import compute_cost_usd_micro
from lite_horse.web.permissions import PermissionBroker, PermissionRequest
from lite_horse.web.sse import (
    encode_event,
    event_delta,
    event_done,
    event_error,
    event_permission_request,
    event_tool_call,
    event_tool_output,
    event_usage,
)

_log = logging.getLogger(__name__)
_slog = get_logger("lite_horse.turns")


@dataclass
class TurnRequest:
    """Request payload after parse + auth."""

    user_id: str
    session_key: str
    text: str
    permission_mode: str = "auto"
    attachments: list[dict[str, Any]] | None = None
    command: str | None = None
    model: str | None = None


@dataclass
class TurnUsage:
    """Snapshot of one turn's token + cost totals.

    Surfaced via :class:`_TurnHandle.usage` after :func:`stream_turn_to_sse`
    yields its terminal ``done`` event so the route handler can persist
    a ``usage_events`` row in a fresh transaction.
    """

    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd_micro: int = 0


class StreamEventLike(Protocol):
    """Minimal shape we accept from a streaming turn source."""

    # Each event implements one of:
    #   - .text               (delta)
    #   - .name + .arguments  (tool call)
    #   - .name + .output     (tool output)
    #   - .result             (terminal)
    pass


TurnRunner = Callable[[TurnRequest], AsyncIterator[Any]]


@dataclass
class _TurnHandle:
    turn_id: str
    task: asyncio.Task[Any] | None = None
    aborted: bool = False
    queue: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    usage: TurnUsage = field(default_factory=TurnUsage)


class TurnRegistry:
    """Process-wide registry of in-flight turns (for abort + decisions)."""

    def __init__(self) -> None:
        self._turns: dict[str, _TurnHandle] = {}

    def new(self) -> _TurnHandle:
        turn_id = "turn_" + secrets.token_hex(12)
        handle = _TurnHandle(turn_id=turn_id)
        self._turns[turn_id] = handle
        return handle

    def get(self, turn_id: str) -> _TurnHandle | None:
        return self._turns.get(turn_id)

    def remove(self, turn_id: str) -> None:
        self._turns.pop(turn_id, None)

    async def abort(self, turn_id: str) -> bool:
        handle = self._turns.get(turn_id)
        if handle is None:
            return False
        handle.aborted = True
        if handle.task is not None and not handle.task.done():
            handle.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await handle.task
        return True


def make_turn_id() -> str:
    return "turn_" + secrets.token_hex(12)


async def stream_turn_to_sse(  # noqa: PLR0915  -- multiplex state stays in one frame
    *,
    request: TurnRequest,
    runner: TurnRunner,
    broker: PermissionBroker,
    handle: _TurnHandle,
) -> AsyncIterator[bytes]:
    """Drive the agent stream + permission queue, emitting SSE bytes.

    The runner is consumed in a child task so we can multiplex it with
    the broker's pending-events queue. On abort the child task is
    cancelled and an ``error`` event is emitted before ``done``.
    """
    stream_q: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()

    async def _drain_runner() -> None:
        try:
            async for ev in runner(request):
                await stream_q.put(("event", ev))
        except asyncio.CancelledError:
            await stream_q.put(("aborted", None))
            raise
        except Exception as exc:
            await stream_q.put(("error", exc))
        finally:
            await stream_q.put(None)

    async def _drain_broker() -> None:
        try:
            while True:
                req = await broker.pending_events(handle.turn_id)
                await stream_q.put(("permission", req))
        except asyncio.CancelledError:
            return

    runner_task = asyncio.create_task(_drain_runner())
    broker_task = asyncio.create_task(_drain_broker())
    handle.task = runner_task

    bind_log_context(turn_id=handle.turn_id, session_key=request.session_key)
    emit_metric(
        "turns_total",
        1,
        dimensions={"model": request.model or "default"},
    )
    final_text = ""
    try:
        while True:
            item = await stream_q.get()
            if item is None:
                break
            kind, payload = item
            if kind == "event":
                async for chunk in _translate_event(payload):
                    yield chunk
                if payload.__class__.__name__ == "StreamDone":
                    final_text = getattr(payload.result, "final_output", "") or ""
                    in_tok = int(getattr(payload.result, "input_tokens", 0) or 0)
                    out_tok = int(getattr(payload.result, "output_tokens", 0) or 0)
                    cached_tok = int(
                        getattr(payload.result, "cached_input_tokens", 0) or 0
                    )
                    model_name = request.model or ""
                    cost_micro = compute_cost_usd_micro(
                        model=model_name,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        cached_input_tokens=cached_tok,
                    )
                    handle.usage = TurnUsage(
                        model=model_name or None,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        cached_input_tokens=cached_tok,
                        cost_usd_micro=cost_micro,
                    )
                    yield event_usage(
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        cost_usd_micro=cost_micro,
                    )
                    emit_metric(
                        "tokens_total",
                        in_tok + out_tok,
                        dimensions={"model": model_name or "default"},
                    )
                    emit_metric(
                        "cost_usd_micro",
                        cost_micro,
                        unit="None",
                        dimensions={"model": model_name or "default"},
                    )
                    _slog.info(
                        "turn_complete",
                        model=model_name or None,
                        tokens_in=in_tok,
                        tokens_out=out_tok,
                        cost_usd_micro=cost_micro,
                    )
            elif kind == "permission":
                req: PermissionRequest = payload
                yield event_permission_request(
                    tool_call_id=req.tool_call_id,
                    tool=req.tool,
                    args=req.args,
                )
            elif kind == "error":
                msg = str(payload) or payload.__class__.__name__
                emit_metric(
                    "errors_total", 1, dimensions={"error_kind": "INTERNAL"}
                )
                _slog.error("turn_error", error_kind="INTERNAL", message=msg)
                yield event_error(kind="INTERNAL", message=msg)
                break
            elif kind == "aborted":
                emit_metric(
                    "errors_total", 1, dimensions={"error_kind": "UNAVAILABLE"}
                )
                yield event_error(kind="UNAVAILABLE", message="turn aborted")
                break
    finally:
        broker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await broker_task
        if not runner_task.done():
            runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await runner_task
        broker.cancel_turn(handle.turn_id)
        yield event_done(turn_id=handle.turn_id, final_text=final_text)


async def _translate_event(ev: Any) -> AsyncIterator[bytes]:
    """Map :mod:`lite_horse.api` stream events to SSE bytes.

    Duck-typed on attribute names so tests can pass simple stand-ins
    without importing the SDK.
    """
    name = ev.__class__.__name__
    if name == "StreamDelta":
        text = getattr(ev, "text", "")
        if text:
            yield event_delta(text)
        return
    if name == "StreamToolCall":
        tool_call_id = getattr(ev, "id", None) or _synthesise_tool_call_id(ev)
        yield event_tool_call(
            id=tool_call_id,
            name=getattr(ev, "name", "tool"),
            args=getattr(ev, "arguments", ""),
        )
        return
    if name == "StreamToolOutput":
        tool_call_id = getattr(ev, "id", None) or _synthesise_tool_call_id(ev)
        yield event_tool_output(
            id=tool_call_id,
            output=str(getattr(ev, "output", "")),
        )
        return
    if name == "StreamDone":
        # caller emits usage + done; nothing here
        return
    # Unknown event: stay silent rather than break the stream.
    _log.debug("turns: ignoring stream event of type %s", name)
    return


def _synthesise_tool_call_id(ev: Any) -> str:
    """Pick a stable id when the event lacks one — fall back on the name."""
    return f"tc_{getattr(ev, 'name', 'tool')}_{id(ev) & 0xFFFF:04x}"


async def collect_sse(
    iterator: AsyncIterator[bytes],
) -> tuple[bytes, list[tuple[str, str]]]:
    """Concatenate a streaming turn into bytes + a parsed event list.

    Used by the non-streaming JSON route and the idempotency replay code
    path. The parsed list is ``[(event_name, raw_data), ...]``.
    """
    chunks: list[bytes] = []
    async for chunk in iterator:
        chunks.append(chunk)
    raw = b"".join(chunks)
    parsed = _parse_sse(raw)
    return raw, parsed


def _parse_sse(raw: bytes) -> list[tuple[str, str]]:
    text = raw.decode("utf-8", errors="replace")
    out: list[tuple[str, str]] = []
    name = ""
    data_lines: list[str] = []
    for line in text.split("\n"):
        if line == "":
            if name:
                out.append((name, "\n".join(data_lines)))
            name = ""
            data_lines = []
            continue
        if line.startswith("event: "):
            name = line[len("event: "):].strip()
        elif line.startswith("data: "):
            data_lines.append(line[len("data: "):])
    return out


__all__ = [
    "TurnRegistry",
    "TurnRequest",
    "TurnRunner",
    "TurnUsage",
    "_TurnHandle",
    "collect_sse",
    "encode_event",
    "make_turn_id",
    "stream_turn_to_sse",
]
