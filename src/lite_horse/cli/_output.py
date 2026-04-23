"""Output helpers for scripted CLI commands.

Human mode writes plain text to stdout (data) and stderr (progress/errors).
`--json` mode writes NDJSON records to stdout only — one object per line,
nothing else. REPL mode does not use this module.

Rich is imported lazily inside `_human_*` helpers to keep `--help` fast.
"""
from __future__ import annotations

import json
import sys
from typing import Any, TextIO


def _write_ndjson(record: dict[str, Any], *, stream: TextIO) -> None:
    stream.write(json.dumps(record, separators=(",", ":"), default=str))
    stream.write("\n")
    stream.flush()


def emit_result(data: Any, *, json_mode: bool) -> None:
    """Terminal result of a command. Human mode prints the repr-ish form."""
    if json_mode:
        _write_ndjson({"kind": "result", "data": data}, stream=sys.stdout)
        return
    if isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2, default=str))


def emit_item(data: Any, *, json_mode: bool) -> None:
    """One of many streamed items (e.g. a session in `sessions list`)."""
    if json_mode:
        _write_ndjson({"kind": "item", "data": data}, stream=sys.stdout)
        return
    if isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, default=str))


def emit_progress(stage: str, pct: float | None = None, *, json_mode: bool) -> None:
    """Human-visible progress. In json mode goes to stdout NDJSON; human mode
    writes to stderr so it doesn't pollute the data stream."""
    if json_mode:
        payload: dict[str, Any] = {"kind": "progress", "stage": stage}
        if pct is not None:
            payload["pct"] = pct
        _write_ndjson(payload, stream=sys.stdout)
        return
    if pct is None:
        print(stage, file=sys.stderr)
    else:
        print(f"{stage} ({pct * 100:.0f}%)", file=sys.stderr)


def emit_error(message: str, code: int, *, json_mode: bool) -> None:
    if json_mode:
        _write_ndjson(
            {"kind": "error", "code": code, "message": message},
            stream=sys.stdout,
        )
        return
    print(f"error: {message}", file=sys.stderr)
