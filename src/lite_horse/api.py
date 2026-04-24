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
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from agents import Agent, Runner, ToolCallItem, ToolCallOutputItem
from agents.mcp import MCPServer

from lite_horse.agent.errors import ErrorKind, classify
from lite_horse.agent.factory import build_agent, build_mcp_servers
from lite_horse.config import Config, load_config
from lite_horse.core.session_lock import SessionLockRegistry
from lite_horse.sessions.db import SearchHit, SessionDB
from lite_horse.sessions.sdk_session import SDKSession
from lite_horse.sessions.search_tool import bind_db
from lite_horse.skills.source import sync_bundled_skills

__all__ = [
    "RunResult",
    "SearchHit",
    "StreamDelta",
    "StreamDone",
    "StreamEvent",
    "StreamToolCall",
    "StreamToolOutput",
    "end_session",
    "run_turn",
    "run_turn_streaming",
    "search_sessions",
]

log = logging.getLogger(__name__)

# Retry schedule for RATE_LIMIT / NETWORK failures in ``run_turn``.
# 1 initial attempt + 2 retries; delays 1s, 4s (exponential base 4).
_MAX_RUN_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 1.0
_RETRY_BACKOFF_FACTOR = 4.0


def _retry_delay(attempt: int) -> float:
    """Seconds to sleep before ``attempt`` (1-indexed past the first)."""
    exponent = max(0, attempt - 1)
    return _RETRY_BASE_SECONDS * (_RETRY_BACKOFF_FACTOR ** exponent)


@dataclass
class RunResult:
    """The webapp-facing summary of one completed turn."""

    final_output: str
    session_key: str
    turn_count: int
    tool_calls: int


@dataclass
class StreamDelta:
    """Incremental text chunk from the model."""

    text: str


@dataclass
class StreamToolCall:
    """A tool was invoked (announce-time event, no output yet)."""

    name: str
    arguments: str


@dataclass
class StreamToolOutput:
    """Output of a previously-announced tool call."""

    name: str
    output: str


@dataclass
class StreamDone:
    """Terminal event carrying the same summary as :func:`run_turn`."""

    result: RunResult


StreamEvent = StreamDelta | StreamToolCall | StreamToolOutput | StreamDone


_DB: SessionDB | None = None
_AGENT: Agent[Any] | None = None
_CFG: Config | None = None
_MCP_SERVERS: list[MCPServer] = []
_LOCKS = SessionLockRegistry()
_INIT_LOCK = asyncio.Lock()


async def _ensure_ready() -> tuple[SessionDB, Agent[Any], Config]:
    """Materialize the process-wide singletons on first call. Idempotent.

    MCP servers declared in ``config.mcp_servers`` are connected once here and
    kept alive for the process lifetime; their ``cache_tools_list`` then
    actually caches across turns.
    """
    global _DB, _AGENT, _CFG, _MCP_SERVERS
    if _DB is not None and _AGENT is not None and _CFG is not None:
        return _DB, _AGENT, _CFG
    async with _INIT_LOCK:
        if _DB is None or _AGENT is None or _CFG is None:
            sync_bundled_skills()
            cfg = load_config()
            db = SessionDB()
            bind_db(db)
            mcp_servers = build_mcp_servers(cfg)
            for server in mcp_servers:
                try:
                    await server.connect()  # type: ignore[no-untyped-call]
                except Exception:
                    log.exception("MCP server %s failed to connect; skipping", server.name)
            agent = build_agent(config=cfg, mcp_servers=mcp_servers)
            _DB, _AGENT, _CFG, _MCP_SERVERS = db, agent, cfg, mcp_servers
    assert _DB is not None and _AGENT is not None and _CFG is not None
    return _DB, _AGENT, _CFG


async def shutdown() -> None:
    """Close any connected MCP servers. Optional; intended for graceful exits."""
    global _MCP_SERVERS  # noqa: PLW0603
    for server in _MCP_SERVERS:
        try:
            await server.cleanup()  # type: ignore[no-untyped-call]
        except Exception:
            log.exception("MCP server %s cleanup failed", server.name)
    _MCP_SERVERS = []


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
        turns = max_turns or cfg.agent.max_turns
        attempt = 0
        while True:
            attempt += 1
            try:
                result = await Runner.run(
                    agent,
                    user_text,
                    session=session,  # type: ignore[arg-type]
                    max_turns=turns,
                )
                break
            except Exception as exc:
                classified = classify(exc)
                if classified.retryable and attempt < _MAX_RUN_ATTEMPTS:
                    delay = _retry_delay(attempt)
                    log.warning(
                        "run_turn %s retry %d/%d in %.1fs: %s",
                        classified.kind,
                        attempt,
                        _MAX_RUN_ATTEMPTS - 1,
                        delay,
                        classified.summary,
                    )
                    await asyncio.sleep(delay)
                    continue
                if classified.kind is ErrorKind.CONTEXT_OVERFLOW:
                    log.error(
                        "run_turn context overflow on %s: %s",
                        session_key,
                        classified.summary,
                    )
                elif classified.kind is ErrorKind.MODEL_REFUSAL:
                    log.warning(
                        "run_turn model refusal on %s: %s",
                        session_key,
                        classified.summary,
                    )
                elif classified.kind is ErrorKind.UNKNOWN:
                    log.exception("run_turn unknown failure on %s", session_key)
                raise
    tool_calls = sum(1 for item in result.new_items if isinstance(item, ToolCallItem))
    return RunResult(
        final_output=str(result.final_output),
        session_key=session_key,
        turn_count=len(result.raw_responses),
        tool_calls=tool_calls,
    )


def _tool_call_descriptor(raw_item: Any) -> tuple[str, str]:
    """Best-effort ``(name, arguments_json_or_str)`` from a raw tool-call item.

    The SDK's ``raw_item`` is provider-shaped (Responses API tool calls). We
    duck-type rather than import every concrete model, so unknown shapes fall
    back to ``("tool", "")`` instead of crashing the stream.
    """
    name = getattr(raw_item, "name", None) or getattr(raw_item, "type", None) or "tool"
    args = getattr(raw_item, "arguments", None)
    if args is None:
        args = getattr(raw_item, "input", None)
    if args is None:
        args = ""
    if not isinstance(args, str):
        try:
            args = json.dumps(args, default=str)
        except Exception:
            args = str(args)
    return str(name), args


async def run_turn_streaming(
    *,
    session_key: str,
    user_text: str,
    source: str = "web",
    user_id: str | None = None,
    max_turns: int | None = None,
) -> AsyncIterator[StreamEvent]:
    """Run one turn, yielding incremental events.

    Emits :class:`StreamDelta` for each text chunk, :class:`StreamToolCall` /
    :class:`StreamToolOutput` around tool invocations, and one terminal
    :class:`StreamDone` carrying the same summary as :func:`run_turn`.

    Unlike :func:`run_turn`, this path does **not** auto-retry transient
    failures: once we have started streaming bytes to a renderer we cannot
    rewind, so errors propagate to the caller. The caller is expected to be
    interactive (REPL) and can re-issue the turn.
    """
    db, agent, cfg = await _ensure_ready()
    lock = _LOCKS.get(session_key)
    async with lock:
        session = SDKSession(
            session_key, db, source=source, user_id=user_id, model=cfg.model
        )
        turns = max_turns or cfg.agent.max_turns
        try:
            streaming = Runner.run_streamed(
                agent,
                user_text,
                session=session,  # type: ignore[arg-type]
                max_turns=turns,
            )
        except Exception as exc:
            classified = classify(exc)
            log.warning("run_turn_streaming setup failed (%s): %s",
                        classified.kind, classified.summary)
            raise

        tool_calls = 0
        try:
            async for event in streaming.stream_events():
                if event.type == "raw_response_event":
                    data = event.data
                    if getattr(data, "type", None) == "response.output_text.delta":
                        delta = getattr(data, "delta", "")
                        if delta:
                            yield StreamDelta(text=delta)
                elif event.type == "run_item_stream_event":
                    item = event.item
                    if isinstance(item, ToolCallItem):
                        tool_calls += 1
                        name, args = _tool_call_descriptor(item.raw_item)
                        yield StreamToolCall(name=name, arguments=args)
                    elif isinstance(item, ToolCallOutputItem):
                        name, _ = _tool_call_descriptor(item.raw_item)
                        yield StreamToolOutput(name=name, output=str(item.output))
                # AgentUpdatedStreamEvent: ignore for now (single-agent setup)
        except Exception as exc:
            classified = classify(exc)
            if classified.kind is ErrorKind.UNKNOWN:
                log.exception("run_turn_streaming unknown failure on %s", session_key)
            else:
                log.warning("run_turn_streaming %s on %s: %s",
                            classified.kind, session_key, classified.summary)
            raise

        final_text = ""
        try:
            final_text = str(streaming.final_output)
        except Exception:
            log.exception("run_turn_streaming: failed to read final_output")
        result = RunResult(
            final_output=final_text,
            session_key=session_key,
            turn_count=len(streaming.raw_responses) if hasattr(streaming, "raw_responses") else 0,
            tool_calls=tool_calls,
        )
        yield StreamDone(result=result)


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
