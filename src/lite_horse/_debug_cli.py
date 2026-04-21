"""Minimal debug REPL. Not a product surface — use ``lite_horse.api`` instead.

Registered as ``litehorse-debug`` for local development only. Plain
``input()``/``print()``, no click/rich. Thin wrapper around
:func:`lite_horse.api.run_turn`.
"""
from __future__ import annotations

import asyncio
import uuid

from lite_horse.api import end_session, run_turn


async def _repl() -> None:
    key = f"agent:main:debug:private:{uuid.uuid4().hex[:12]}"
    print(f"session: {key}  (/exit to quit)")
    try:
        while True:
            try:
                line = await asyncio.to_thread(input, "you: ")
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip() in {"/exit", "/quit", ":q"}:
                break
            if not line.strip():
                continue
            r = await run_turn(session_key=key, user_text=line, source="debug")
            print(f"horse: {r.final_output}\n")
    finally:
        await end_session(key)


def main() -> None:
    asyncio.run(_repl())
