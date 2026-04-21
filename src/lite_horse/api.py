"""Public surface consumed by the webapp.

Everything else in ``lite_horse`` is internal. The webapp imports from here:

    from lite_horse.api import run_turn, end_session, search_sessions, RunResult
    from lite_horse.core.session_key import build_session_key

Invariants
----------
- One process-wide :class:`SessionDB`, bound once to the ``session_search`` tool.
- One cached :class:`Agent`; tests monkeypatch ``_AGENT`` to override.
- Runs with the same ``session_key`` serialize on a per-key ``asyncio.Lock``;
  runs on distinct keys proceed in parallel.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from agents import Agent, Runner, ToolCallItem

from lite_horse.agent.factory import build_agent
from lite_horse.config import Config, load_config
from lite_horse.core.session_lock import SessionLockRegistry
from lite_horse.sessions.db import SearchHit, SessionDB
from lite_horse.sessions.sdk_session import SDKSession
from lite_horse.sessions.search_tool import bind_db
from lite_horse.skills.source import sync_bundled_skills

__all__ = [
    "RunResult",
    "SearchHit",
    "end_session",
    "run_turn",
    "search_sessions",
]

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    """The webapp-facing summary of one completed turn."""

    final_output: str
    session_key: str
    turn_count: int
    tool_calls: int


_DB: SessionDB | None = None
_AGENT: Agent[Any] | None = None
_CFG: Config | None = None
_LOCKS = SessionLockRegistry()
_INIT_LOCK = asyncio.Lock()


async def _ensure_ready() -> tuple[SessionDB, Agent[Any], Config]:
    """Materialize the process-wide singletons on first call. Idempotent."""
    global _DB, _AGENT, _CFG
    if _DB is not None and _AGENT is not None and _CFG is not None:
        return _DB, _AGENT, _CFG
    async with _INIT_LOCK:
        if _DB is None or _AGENT is None or _CFG is None:
            sync_bundled_skills()
            cfg = load_config()
            db = SessionDB()
            bind_db(db)
            agent = build_agent(config=cfg)
            _DB, _AGENT, _CFG = db, agent, cfg
    assert _DB is not None and _AGENT is not None and _CFG is not None
    return _DB, _AGENT, _CFG


async def run_turn(
    *,
    session_key: str,
    user_text: str,
    source: str = "web",
    user_id: str | None = None,
    max_turns: int | None = None,
) -> RunResult:
    """Run one user turn against the agent, returning a summary.

    Same-``session_key`` calls serialize; distinct keys run in parallel. The
    underlying ``SDKSession`` is created on demand and persisted to the
    process-wide ``SessionDB``.
    """
    db, agent, cfg = await _ensure_ready()
    lock = _LOCKS.get(session_key)
    async with lock:
        session = SDKSession(
            session_key, db, source=source, user_id=user_id, model=cfg.model
        )
        result = await Runner.run(
            agent,
            user_text,
            session=session,  # type: ignore[arg-type]
            max_turns=max_turns or cfg.agent.max_turns,
        )
    tool_calls = sum(1 for item in result.new_items if isinstance(item, ToolCallItem))
    return RunResult(
        final_output=str(result.final_output),
        session_key=session_key,
        turn_count=len(result.raw_responses),
        tool_calls=tool_calls,
    )


async def end_session(session_key: str, *, reason: str = "user_exit") -> None:
    """Stamp ``ended_at`` + ``end_reason`` on the session row."""
    db, _agent, _cfg = await _ensure_ready()
    db.end_session(session_key, end_reason=reason)


def search_sessions(
    query: str, *, limit: int = 20, source: str | None = None
) -> list[SearchHit]:
    """FTS5 lookup across persisted messages. Returns at most ``limit`` hits."""
    if _DB is None:
        raise RuntimeError(
            "lite_horse.api not initialized; call run_turn() at least once first"
        )
    return _DB.search_messages(
        query,
        limit=min(max(1, int(limit)), 50),
        source_filter=[source] if source else None,
    )
