"""Tests for the autonomous skill-creation hook (Phase 4)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from lite_horse.agent import evolution as evo_mod
from lite_horse.agent.evolution import EvolutionHook


@dataclass
class _FakeCtx:
    turn_input: list[Any] = field(default_factory=list)


class _Sentinel:
    """Marker tool value — the hook passes it through untouched."""


def _stub_runner(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace ``Runner.run`` with a capturing stub and return the call log."""
    calls: list[dict[str, Any]] = []

    async def fake_run(agent: Any, prompt: str, **kwargs: Any) -> Any:
        calls.append({"agent": agent, "prompt": prompt, "kwargs": kwargs})
        return None

    monkeypatch.setattr(evo_mod.Runner, "run", fake_run)
    return calls


async def _tick(hook: EvolutionHook, ctx: _FakeCtx, n: int) -> None:
    for _ in range(n):
        await hook.on_tool_end(ctx, agent=None, tool=_Sentinel(), result="ok")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_distiller_not_called_below_threshold(
    litehorse_home: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    calls = _stub_runner(monkeypatch)
    hook = EvolutionHook(min_tool_calls=5)
    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "hi"}])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _tick(hook, ctx, 4)
    await hook.on_end(ctx, agent=None, output="done")  # type: ignore[arg-type]

    assert calls == []


@pytest.mark.asyncio
async def test_distiller_called_at_threshold(
    litehorse_home: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    calls = _stub_runner(monkeypatch)
    hook = EvolutionHook(min_tool_calls=5)
    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "plan a trip"}])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _tick(hook, ctx, 5)
    await hook.on_end(ctx, agent=None, output="trip planned")  # type: ignore[arg-type]

    assert len(calls) == 1
    payload = json.loads(calls[0]["prompt"])
    assert payload == {
        "user_request": "plan a trip",
        "tool_calls_used": 5,
        "final_output_excerpt": "trip planned",
    }
    assert calls[0]["kwargs"].get("max_turns") == 4


@pytest.mark.asyncio
async def test_distiller_exceptions_are_swallowed(
    litehorse_home: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home

    async def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("distiller exploded")

    monkeypatch.setattr(evo_mod.Runner, "run", boom)
    hook = EvolutionHook(min_tool_calls=3)
    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "x"}])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _tick(hook, ctx, 3)
    # Must not raise.
    await hook.on_end(ctx, agent=None, output="y")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_on_start_resets_counter_between_runs(
    litehorse_home: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    calls = _stub_runner(monkeypatch)
    hook = EvolutionHook(min_tool_calls=3)
    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "first"}])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _tick(hook, ctx, 3)
    await hook.on_end(ctx, agent=None, output="first-done")  # type: ignore[arg-type]
    assert len(calls) == 1

    # New run: only 1 tool call — should NOT invoke distiller.
    ctx.turn_input = [{"role": "user", "content": "second"}]
    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _tick(hook, ctx, 1)
    await hook.on_end(ctx, agent=None, output="second-done")  # type: ignore[arg-type]
    assert len(calls) == 1  # still just the first call


@pytest.mark.asyncio
async def test_user_request_extracted_from_structured_content(
    litehorse_home: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    calls = _stub_runner(monkeypatch)
    hook = EvolutionHook(min_tool_calls=2)
    ctx = _FakeCtx(
        turn_input=[
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "structured request"}],
            }
        ]
    )

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _tick(hook, ctx, 2)
    await hook.on_end(ctx, agent=None, output="ok")  # type: ignore[arg-type]

    payload = json.loads(calls[0]["prompt"])
    assert payload["user_request"] == "structured request"


@pytest.mark.asyncio
async def test_missing_user_request_is_none_not_crash(
    litehorse_home: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    calls = _stub_runner(monkeypatch)
    hook = EvolutionHook(min_tool_calls=2)
    ctx = _FakeCtx(turn_input=[])  # no items at all

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _tick(hook, ctx, 2)
    await hook.on_end(ctx, agent=None, output="done")  # type: ignore[arg-type]

    payload = json.loads(calls[0]["prompt"])
    assert payload["user_request"] is None
    assert payload["tool_calls_used"] == 2
