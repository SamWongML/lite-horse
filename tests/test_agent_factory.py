"""Tests for the agent factory + composite hooks (Phase 7)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from lite_horse.agent import evolution as evo_mod
from lite_horse.agent.factory import LiteHorseHooks, build_agent
from lite_horse.config import Config


@dataclass
class _FakeCtx:
    turn_input: list[Any] = field(default_factory=list)


class _Sentinel:
    """Marker tool value — the hook ignores it."""


# ---------- LiteHorseHooks ----------


@pytest.mark.asyncio
async def test_composite_forwards_on_start_to_both_children() -> None:
    hooks = LiteHorseHooks(max_turns=10, model="gpt-test")
    # Seed both sub-hooks with non-default state, then confirm on_start resets.
    hooks._budget.iteration = 7
    hooks._budget._last_tier = "caution"
    hooks._evo._tool_call_count = 4
    hooks._evo._final_output = "stale"

    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "hi"}])
    await hooks.on_start(ctx, agent=None)  # type: ignore[arg-type]

    assert hooks._budget.iteration == 0
    assert hooks._budget._last_tier is None
    assert hooks._evo._tool_call_count == 0
    assert hooks._evo._final_output is None
    assert hooks._evo._user_request == "hi"


@pytest.mark.asyncio
async def test_composite_forwards_on_tool_end_to_both_children() -> None:
    hooks = LiteHorseHooks(max_turns=10, model="gpt-test")
    ctx = _FakeCtx(turn_input=[{"role": "tool", "content": "ok"}])

    await hooks.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await hooks.on_tool_end(ctx, agent=None, tool=_Sentinel(), result="ok")  # type: ignore[arg-type]

    assert hooks._budget.iteration == 1
    assert hooks._evo._tool_call_count == 1


@pytest.mark.asyncio
async def test_composite_only_evolution_sees_on_end(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home

    calls: list[Any] = []

    async def fake_run(agent: Any, prompt: str, **kwargs: Any) -> Any:
        calls.append(prompt)
        return None

    monkeypatch.setattr(evo_mod.Runner, "run", fake_run)

    hooks = LiteHorseHooks(max_turns=10, model="gpt-test")
    # Lower the evo threshold so a single on_tool_end trips it.
    hooks._evo.min_tool_calls = 1

    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "plan"}])
    await hooks.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await hooks.on_tool_end(ctx, agent=None, tool=_Sentinel(), result="ok")  # type: ignore[arg-type]
    await hooks.on_end(ctx, agent=None, output="done")  # type: ignore[arg-type]

    assert len(calls) == 1  # evolution distiller fired


# ---------- build_agent ----------


def test_build_agent_uses_config_values(litehorse_home: Path) -> None:
    del litehorse_home
    cfg = Config.model_validate(
        {
            "model": "gpt-test",
            "model_settings": {
                "reasoning_effort": "high",
                "parallel_tool_calls": False,
            },
            "agent": {"max_turns": 42},
        }
    )
    agent = build_agent(config=cfg)

    assert agent.name == "lite-horse"
    assert agent.model == "gpt-test"
    assert callable(agent.instructions)
    assert agent.model_settings.parallel_tool_calls is False
    assert agent.model_settings.store is True
    assert agent.model_settings.prompt_cache_retention == "24h"
    assert agent.model_settings.reasoning is not None
    assert agent.model_settings.reasoning.effort == "high"

    tool_names = {getattr(t, "name", None) for t in agent.tools}
    assert {"memory", "session_search", "skill_manage"} <= tool_names
    # web_search stays off unless explicitly enabled.
    assert "web_search" not in tool_names
    assert "web_search_preview" not in tool_names

    assert isinstance(agent.hooks, LiteHorseHooks)
    assert agent.hooks._budget.max_turns == 42
    assert agent.hooks._evo.model == "gpt-test"


def test_build_agent_wires_web_search_when_enabled(litehorse_home: Path) -> None:
    del litehorse_home
    cfg = Config.model_validate({"tools": {"web_search": True}})
    agent = build_agent(config=cfg)

    tool_names = {getattr(t, "name", None) for t in agent.tools}
    # The SDK names the hosted tool "web_search_preview" (Responses API).
    assert any(n and n.startswith("web_search") for n in tool_names), tool_names


def test_build_agent_falls_back_to_load_config(litehorse_home: Path) -> None:
    del litehorse_home
    agent = build_agent()
    # Defaults come from DEFAULT_CONFIG_YAML written on first load.
    assert agent.model == "gpt-5.4"
    assert isinstance(agent.hooks, LiteHorseHooks)
    assert agent.hooks._budget.max_turns == 90
