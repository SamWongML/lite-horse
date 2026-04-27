"""SSE event encoder for ``/v1/turns:stream``.

Per the locked HTTP surface, every streamed event has the shape::

    event: <name>
    data: <json>
    \\n

Helpers below produce the seven event kinds defined in the plan
(``delta``, ``tool_call``, ``tool_output``, ``permission_request``,
``usage``, ``error``, ``done``). Each returns ``bytes`` ready to write
to a ``StreamingResponse`` queue.
"""
from __future__ import annotations

import json
from typing import Any

__all__ = [
    "encode_event",
    "event_delta",
    "event_done",
    "event_error",
    "event_permission_request",
    "event_tool_call",
    "event_tool_output",
    "event_usage",
]


def encode_event(name: str, data: dict[str, Any]) -> bytes:
    """Serialise one SSE record. ``data`` is JSON-encoded on a single line."""
    payload = json.dumps(data, separators=(",", ":"), default=str)
    return f"event: {name}\ndata: {payload}\n\n".encode()


def event_delta(text: str) -> bytes:
    return encode_event("delta", {"text": text})


def event_tool_call(*, id: str, name: str, args: Any) -> bytes:
    return encode_event("tool_call", {"id": id, "name": name, "args": args})


def event_tool_output(*, id: str, output: str) -> bytes:
    return encode_event("tool_output", {"id": id, "output": output})


def event_permission_request(
    *, tool_call_id: str, tool: str, args: Any
) -> bytes:
    return encode_event(
        "permission_request",
        {"tool_call_id": tool_call_id, "tool": tool, "args": args},
    )


def event_usage(
    *,
    input_tokens: int,
    output_tokens: int,
    cost_usd_micro: int = 0,
) -> bytes:
    return encode_event(
        "usage",
        {
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cost_usd_micro": int(cost_usd_micro),
        },
    )


def event_error(*, kind: str, message: str, retry_after_ms: int | None = None) -> bytes:
    body: dict[str, Any] = {"kind": kind, "message": message}
    if retry_after_ms is not None:
        body["retry_after_ms"] = int(retry_after_ms)
    return encode_event("error", body)


def event_done(*, turn_id: str, final_text: str) -> bytes:
    return encode_event("done", {"turn_id": turn_id, "final_text": final_text})
