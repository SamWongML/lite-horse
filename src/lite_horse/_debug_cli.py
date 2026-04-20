"""Minimal debug REPL. Not a product surface — use ``lite_horse.api`` instead.

Registered as ``litehorse-debug`` for local development only. Plain
``input()``/``print()``, no click/rich. Rewritten on ``api.run_turn`` in Phase 16.
"""
from __future__ import annotations

import asyncio
import uuid

from agents import Runner

from lite_horse.agent.factory import build_agent
from lite_horse.config import load_config
from lite_horse.sessions.db import SessionDB
from lite_horse.sessions.sdk_session import SDKSession
from lite_horse.sessions.search_tool import bind_db
from lite_horse.skills.source import sync_bundled_skills


async def _repl() -> None:
    sync_bundled_skills()
    cfg = load_config()
    db = SessionDB()
    bind_db(db)
    sid = f"debug-{uuid.uuid4().hex[:12]}"
    session = SDKSession(sid, db, source="debug", model=cfg.model)
    agent = build_agent(config=cfg)
    print(f"session: {sid}  (/exit to quit)")
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
            r = await Runner.run(agent, line, session=session, max_turns=cfg.agent.max_turns)  # type: ignore[arg-type]
            print(f"horse: {r.final_output}\n")
    finally:
        db.end_session(sid)


def main() -> None:
    asyncio.run(_repl())
