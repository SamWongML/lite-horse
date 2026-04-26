"""Tests for the public ``lite_horse.api`` surface (Phase 16)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import openai
import pytest
from httpx import Request, Response

from lite_horse import api as api_mod
from lite_horse.api import (
    RunResult,
    end_session,
    run_turn,
    search_sessions,
)
from lite_horse.core.session_key import build_session_key


class _FakeToolCallItem:
    """Minimal stand-in recognized by ``isinstance`` in ``api.run_turn``."""


class _FakeResult:
    def __init__(
        self,
        *,
        final_output: str = "ok",
        tool_calls: int = 0,
        raw_responses: int = 1,
    ) -> None:
        self.final_output = final_output
        self.new_items: list[Any] = [_FakeToolCallItem() for _ in range(tool_calls)]
        self.raw_responses: list[Any] = [object() for _ in range(raw_responses)]


def _patch_tool_call_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """Teach ``run_turn`` to count our fake tool-call items."""
    monkeypatch.setattr(api_mod, "ToolCallItem", _FakeToolCallItem)


# ---------- happy path ----------


@pytest.mark.asyncio
async def test_run_turn_returns_populated_result(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_call_item(monkeypatch)

    captured: dict[str, Any] = {}

    async def fake_run(agent: Any, text: str, **kwargs: Any) -> _FakeResult:
        captured["agent"] = agent
        captured["text"] = text
        captured["session_key"] = kwargs["session"].session_id
        captured["max_turns"] = kwargs.get("max_turns")
        return _FakeResult(final_output="hello", tool_calls=3, raw_responses=2)

    monkeypatch.setattr(api_mod.Runner, "run", fake_run)

    key = build_session_key(platform="web", chat_type="private", chat_id=7)
    result = await run_turn(session_key=key, user_text="hi", source="web")

    assert isinstance(result, RunResult)
    assert result.final_output == "hello"
    assert result.session_key == key
    assert result.tool_calls == 3
    assert result.turn_count == 2
    assert captured["text"] == "hi"
    assert captured["session_key"] == key


@pytest.mark.asyncio
async def test_run_turn_respects_explicit_max_turns(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_call_item(monkeypatch)

    seen: dict[str, Any] = {}

    async def fake_run(agent: Any, text: str, **kwargs: Any) -> _FakeResult:
        del agent, text
        seen["max_turns"] = kwargs.get("max_turns")
        return _FakeResult()

    monkeypatch.setattr(api_mod.Runner, "run", fake_run)

    await run_turn(session_key="k1", user_text="hi", max_turns=17)
    assert seen["max_turns"] == 17


# ---------- same-key serialization ----------


@pytest.mark.asyncio
async def test_same_key_runs_serialize(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_call_item(monkeypatch)

    entered = asyncio.Event()
    release = asyncio.Event()
    concurrent = 0
    max_concurrent = 0

    async def fake_run(agent: Any, text: str, **_kw: Any) -> _FakeResult:
        del agent, text
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        entered.set()
        await release.wait()
        concurrent -= 1
        return _FakeResult()

    monkeypatch.setattr(api_mod.Runner, "run", fake_run)

    key = "agent:main:web:private:1"
    t1 = asyncio.create_task(run_turn(session_key=key, user_text="a"))
    t2 = asyncio.create_task(run_turn(session_key=key, user_text="b"))

    await entered.wait()
    # Give t2 a few event-loop ticks to confirm it's stuck on the lock.
    for _ in range(20):
        await asyncio.sleep(0)
    assert concurrent == 1
    assert not t2.done()

    release.set()
    await asyncio.gather(t1, t2)
    assert max_concurrent == 1


# ---------- distinct-key parallelism ----------


@pytest.mark.asyncio
async def test_distinct_keys_run_in_parallel(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_call_item(monkeypatch)

    in_flight: set[str] = set()
    peak = 0
    release = asyncio.Event()

    async def fake_run(agent: Any, text: str, **kwargs: Any) -> _FakeResult:
        del agent, text
        nonlocal peak
        sid = kwargs["session"].session_id
        in_flight.add(sid)
        peak = max(peak, len(in_flight))
        # Wait until both tasks have entered before releasing either.
        await release.wait()
        in_flight.discard(sid)
        return _FakeResult()

    monkeypatch.setattr(api_mod.Runner, "run", fake_run)

    t1 = asyncio.create_task(run_turn(session_key="k-a", user_text="x"))
    t2 = asyncio.create_task(run_turn(session_key="k-b", user_text="y"))

    for _ in range(50):
        if len(in_flight) == 2:
            break
        await asyncio.sleep(0)
    assert len(in_flight) == 2

    release.set()
    await asyncio.gather(t1, t2)
    assert peak == 2


# ---------- end_session ----------


@pytest.mark.asyncio
async def test_end_session_writes_ended_at(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_call_item(monkeypatch)

    async def fake_run(agent: Any, text: str, **_kw: Any) -> _FakeResult:
        del agent, text
        return _FakeResult()

    monkeypatch.setattr(api_mod.Runner, "run", fake_run)

    key = "agent:main:web:private:42"
    await run_turn(session_key=key, user_text="hi")
    await end_session(key, reason="test_done")

    assert api_mod._DB is not None
    meta = api_mod._DB.get_session_meta(key)
    assert meta is not None
    assert meta["ended_at"] is not None
    assert meta["end_reason"] == "test_done"


# ---------- search_sessions ----------


@pytest.mark.asyncio
async def test_search_sessions_returns_hits_after_run(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_call_item(monkeypatch)

    async def fake_run(agent: Any, text: str, **kwargs: Any) -> _FakeResult:
        del agent
        sess = kwargs["session"]
        await sess.add_items(
            [
                {"role": "user", "content": text},
                {"role": "assistant", "content": "stratospheric marker"},
            ]
        )
        return _FakeResult(final_output="stratospheric marker")

    monkeypatch.setattr(api_mod.Runner, "run", fake_run)

    await run_turn(session_key="k-search", user_text="hello")

    hits = search_sessions("stratospheric")
    assert any("stratospheric" in h.snippet for h in hits)


def test_search_sessions_requires_initialization(
    litehorse_home: Path,
) -> None:
    del litehorse_home
    with pytest.raises(RuntimeError, match="not initialized"):
        search_sessions("anything")


# ---------- Phase 22: structured error classifier ----------


def _rate_limit() -> openai.RateLimitError:
    resp = Response(429, request=Request("POST", "https://api.openai.com/v1/x"))
    return openai.RateLimitError("slow down", response=resp, body=None)


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_call_item(monkeypatch)

    calls = 0

    async def fake_run(agent: Any, text: str, **_kw: Any) -> _FakeResult:
        del agent, text
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _rate_limit()
        return _FakeResult(final_output="ok")

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(api_mod.Runner, "run", fake_run)
    monkeypatch.setattr(api_mod.asyncio, "sleep", fake_sleep)

    result = await run_turn(session_key="k-rl", user_text="hi")

    assert result.final_output == "ok"
    assert calls == 3
    # Exponential backoff 1s, 4s between the three attempts.
    assert sleeps == [1.0, 4.0]


@pytest.mark.asyncio
async def test_rate_limit_exhausts_retries_and_raises(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_call_item(monkeypatch)

    calls = 0

    async def fake_run(agent: Any, text: str, **_kw: Any) -> _FakeResult:
        del agent, text
        nonlocal calls
        calls += 1
        raise _rate_limit()

    async def fake_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr(api_mod.Runner, "run", fake_run)
    monkeypatch.setattr(api_mod.asyncio, "sleep", fake_sleep)

    with pytest.raises(openai.RateLimitError):
        await run_turn(session_key="k-rl-out", user_text="hi")
    assert calls == 3


@pytest.mark.asyncio
async def test_model_refusal_surfaces_on_first_call(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_call_item(monkeypatch)

    calls = 0

    async def fake_run(agent: Any, text: str, **_kw: Any) -> _FakeResult:
        del agent, text
        nonlocal calls
        calls += 1
        raise openai.ContentFilterFinishReasonError()

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(api_mod.Runner, "run", fake_run)
    monkeypatch.setattr(api_mod.asyncio, "sleep", fake_sleep)

    with pytest.raises(openai.ContentFilterFinishReasonError):
        await run_turn(session_key="k-refusal", user_text="hi")
    assert calls == 1
    assert slept == []


@pytest.mark.asyncio
async def test_context_overflow_raises_without_retry(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_call_item(monkeypatch)

    calls = 0

    async def fake_run(agent: Any, text: str, **_kw: Any) -> _FakeResult:
        del agent, text
        nonlocal calls
        calls += 1
        resp = Response(400, request=Request("POST", "https://api.openai.com/v1/x"))
        raise openai.BadRequestError(
            "This model's maximum context length is 128000 tokens.",
            response=resp,
            body={"code": "context_length_exceeded"},
        )

    monkeypatch.setattr(api_mod.Runner, "run", fake_run)

    with pytest.raises(openai.BadRequestError):
        await run_turn(session_key="k-ctx", user_text="hi")
    assert calls == 1
