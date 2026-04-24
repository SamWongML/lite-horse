"""Tests for ``run_turn_streaming`` (Phase 27)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from lite_horse import api as api_mod
from lite_horse.api import (
    RunResult,
    StreamDelta,
    StreamDone,
    StreamEvent,
    StreamToolCall,
    StreamToolOutput,
    run_turn_streaming,
)


class _FakeRawDelta:
    def __init__(self, delta: str) -> None:
        self.type = "response.output_text.delta"
        self.delta = delta


class _FakeRawOther:
    def __init__(self) -> None:
        self.type = "response.created"


class _FakeRawStreamEvent:
    def __init__(self, data: Any) -> None:
        self.type = "raw_response_event"
        self.data = data


class _FakeRawToolCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCallItem:
    def __init__(self, name: str, arguments: str) -> None:
        self.raw_item = _FakeRawToolCall(name, arguments)


class _FakeToolOutputItem:
    def __init__(self, name: str, output: str) -> None:
        self.raw_item = _FakeRawToolCall(name, "")
        self.output = output


class _FakeRunItemEvent:
    def __init__(self, item: Any) -> None:
        self.type = "run_item_stream_event"
        self.item = item


class _FakeStreaming:
    def __init__(self, events: list[Any], final: str = "done", raw: int = 1) -> None:
        self._events = events
        self.final_output = final
        self.raw_responses = [object() for _ in range(raw)]

    async def stream_events(self) -> AsyncIterator[Any]:
        for e in self._events:
            yield e


def _patch_tool_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_mod, "ToolCallItem", _FakeToolCallItem)
    monkeypatch.setattr(api_mod, "ToolCallOutputItem", _FakeToolOutputItem)


@pytest.mark.asyncio
async def test_streaming_emits_deltas_and_done(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_classes(monkeypatch)

    events = [
        _FakeRawStreamEvent(_FakeRawDelta("Hello, ")),
        _FakeRawStreamEvent(_FakeRawDelta("world!")),
        _FakeRawStreamEvent(_FakeRawOther()),  # ignored
    ]

    def fake_run_streamed(*_a: Any, **_kw: Any) -> _FakeStreaming:
        return _FakeStreaming(events, final="Hello, world!", raw=2)

    monkeypatch.setattr(api_mod.Runner, "run_streamed", staticmethod(fake_run_streamed))

    received: list[StreamEvent] = []
    async for ev in run_turn_streaming(session_key="k-stream", user_text="hi"):
        received.append(ev)

    deltas = [e for e in received if isinstance(e, StreamDelta)]
    assert [d.text for d in deltas] == ["Hello, ", "world!"]

    done = received[-1]
    assert isinstance(done, StreamDone)
    assert isinstance(done.result, RunResult)
    assert done.result.final_output == "Hello, world!"
    assert done.result.session_key == "k-stream"
    assert done.result.tool_calls == 0
    assert done.result.turn_count == 2


@pytest.mark.asyncio
async def test_streaming_emits_tool_call_and_output(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_classes(monkeypatch)

    events = [
        _FakeRunItemEvent(_FakeToolCallItem("memory", '{"action":"add"}')),
        _FakeRunItemEvent(_FakeToolOutputItem("memory", "ok")),
        _FakeRawStreamEvent(_FakeRawDelta("done")),
    ]

    def fake_run_streamed(*_a: Any, **_kw: Any) -> _FakeStreaming:
        return _FakeStreaming(events, final="done")

    monkeypatch.setattr(api_mod.Runner, "run_streamed", staticmethod(fake_run_streamed))

    received: list[StreamEvent] = []
    async for ev in run_turn_streaming(session_key="k-tool", user_text="x"):
        received.append(ev)

    calls = [e for e in received if isinstance(e, StreamToolCall)]
    outs = [e for e in received if isinstance(e, StreamToolOutput)]
    assert len(calls) == 1 and calls[0].name == "memory"
    assert calls[0].arguments == '{"action":"add"}'
    assert len(outs) == 1 and outs[0].output == "ok"

    done = received[-1]
    assert isinstance(done, StreamDone)
    assert done.result.tool_calls == 1


@pytest.mark.asyncio
async def test_streaming_propagates_error(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _patch_tool_classes(monkeypatch)

    class _Boom(_FakeStreaming):
        async def stream_events(self) -> AsyncIterator[Any]:
            yield _FakeRawStreamEvent(_FakeRawDelta("partial"))
            raise RuntimeError("upstream blew up")

    def fake_run_streamed(*_a: Any, **_kw: Any) -> _Boom:
        return _Boom([], final="")

    monkeypatch.setattr(api_mod.Runner, "run_streamed", staticmethod(fake_run_streamed))

    received: list[StreamEvent] = []
    with pytest.raises(RuntimeError, match="upstream blew up"):
        async for ev in run_turn_streaming(session_key="k-err", user_text="x"):
            received.append(ev)

    assert any(isinstance(e, StreamDelta) for e in received)
