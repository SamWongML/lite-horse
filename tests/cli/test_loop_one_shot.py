"""End-to-end one-shot of ``main_loop`` with the streaming API mocked.

Drives ``main_loop(prompt=...)`` exactly the way the ``repl`` Click command
body does, but stops short of OpenAI by monkeypatching ``run_turn_streaming``.
Asserts:

- stdout receives the streamed text (renderer fed correctly)
- StreamDone tokens are accumulated into ReplState
- Tool announce/output are written to stderr, not stdout
- Exit code is 0 on a clean run
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

import lite_horse.api as api_mod
from lite_horse.api import (
    RunResult,
    StreamDelta,
    StreamDone,
    StreamToolCall,
    StreamToolOutput,
)
from lite_horse.cli.repl import loop as loop_mod


def _make_fake_stream(events: list[Any]) -> Any:
    async def fake(**_kw: Any) -> AsyncIterator[Any]:
        for e in events:
            yield e

    return fake


@pytest.mark.asyncio
async def test_one_shot_streams_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events = [
        StreamDelta(text="Hello, "),
        StreamDelta(text="world!"),
        StreamToolCall(name="memory", arguments=""),
        StreamToolOutput(name="memory", output="ok"),
        StreamDone(result=RunResult(
            final_output="Hello, world!",
            session_key="k1",
            turn_count=1,
            tool_calls=1,
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
        )),
    ]

    fake = _make_fake_stream(events)

    monkeypatch.setattr(api_mod, "run_turn_streaming", fake)
    # Force plain renderer (no rich.live) so capsys can read stdout cleanly.
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(loop_mod, "_load_model_name", lambda: "m-test")

    rc = await loop_mod.main_loop(prompt="say hi", session_key="k1")
    assert rc == 0
    captured = capsys.readouterr()
    assert "Hello, world!" in captured.out
    # tool announce/output go to stderr only (Phase 28 ToolCallPanel)
    assert "→ memory" in captured.err
    assert "↩ memory" in captured.err
    assert "→ memory" not in captured.out
    assert "↩ memory" not in captured.out


@pytest.mark.asyncio
async def test_one_shot_returns_one_on_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def boom(**_kw: Any) -> AsyncIterator[Any]:
        yield  # type: ignore[misc]
        raise RuntimeError("upstream blew up")

    monkeypatch.setattr(api_mod, "run_turn_streaming", boom)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(loop_mod, "_load_model_name", lambda: "m-test")

    rc = await loop_mod.main_loop(prompt="x", session_key="k-err")
    assert rc == 1
    err = capsys.readouterr().err
    assert "[error" in err


@pytest.mark.asyncio
async def test_one_shot_accumulates_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake(**kw: Any) -> AsyncIterator[Any]:
        captured["session_key"] = kw["session_key"]
        yield StreamDelta(text="a")
        yield StreamDone(result=RunResult(
            final_output="a", session_key=kw["session_key"],
            turn_count=1, tool_calls=0,
            input_tokens=5, output_tokens=7, total_tokens=12,
        ))

    # Capture the state instance the loop builds so we can inspect it.
    seen_state: dict[str, Any] = {}
    real_one_turn = loop_mod._run_one_turn

    async def spy(state: Any, text: str) -> None:
        seen_state["state"] = state
        await real_one_turn(state, text)

    monkeypatch.setattr(api_mod, "run_turn_streaming", fake)
    monkeypatch.setattr(loop_mod, "_run_one_turn", spy)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(loop_mod, "_load_model_name", lambda: "m")

    rc = await loop_mod.main_loop(prompt="hi", session_key="k-tok")
    assert rc == 0
    assert captured["session_key"] == "k-tok"
    assert seen_state["state"].total_tokens == 12
