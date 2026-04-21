"""Tests for the autonomous skill-creation hook (Phase 4 + Phase 20 refiner)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from lite_horse.agent import evolution as evo_mod
from lite_horse.agent.evolution import EvolutionHook
from lite_horse.skills import stats as skill_stats
from lite_horse.skills.manage_tool import dispatch as skill_dispatch


@dataclass
class _FakeCtx:
    turn_input: list[Any] = field(default_factory=list)


class _Sentinel:
    """Marker tool value — the hook passes it through untouched."""


@dataclass
class _NamedTool:
    name: str


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


async def _view_skill(hook: EvolutionHook, ctx: _FakeCtx, name: str) -> None:
    payload = json.dumps({"success": True, "name": name, "content": "body"})
    await hook.on_tool_end(
        ctx, agent=None, tool=_NamedTool("skill_view"), result=payload  # type: ignore[arg-type]
    )


async def _tool_error(hook: EvolutionHook, ctx: _FakeCtx, result: str) -> None:
    await hook.on_tool_end(
        ctx, agent=None, tool=_NamedTool("other_tool"), result=result  # type: ignore[arg-type]
    )


def _seed_skill(home: Path, name: str) -> None:
    skill_dispatch(
        "create",
        name=name,
        content=f"---\nname: {name}\ndescription: probe\n---\n\n# {name}\n\nBody.\n",
    )
    # Sanity: dispatch creates the file in LITEHORSE_HOME.
    assert (home / "skills" / name / "SKILL.md").is_file()


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


# --- Phase 20: in-use skill refinement -------------------------------------


@pytest.mark.asyncio
async def test_refiner_fires_on_failure_with_viewed_skill(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_skill(litehorse_home, "probe")
    calls = _stub_runner(monkeypatch)
    hook = EvolutionHook(min_tool_calls=2)
    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "use the probe"}])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _view_skill(hook, ctx, "probe")
    await _tool_error(hook, ctx, '{"success": false, "error": "bad arg"}')
    await hook.on_end(ctx, agent=None, output="failed")  # type: ignore[arg-type]

    assert len(calls) == 1
    agent = calls[0]["agent"]
    assert getattr(agent, "name", None) == "skill-refiner"
    payload = json.loads(calls[0]["prompt"])
    assert payload["skill_name"] == "probe"
    assert "# probe" in payload["skill_markdown"]
    assert payload["error_summary"]
    assert calls[0]["kwargs"].get("max_turns") == 4

    # Outcome stat must show one error for the viewed skill.
    stats = skill_stats.read("probe")
    assert stats is not None
    assert stats["error_count"] == 1
    assert stats["success_count"] == 0


@pytest.mark.asyncio
async def test_refiner_does_not_fire_on_success(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_skill(litehorse_home, "happy")
    calls = _stub_runner(monkeypatch)
    hook = EvolutionHook(min_tool_calls=2)
    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "do a thing"}])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _view_skill(hook, ctx, "happy")
    await _tool_error(hook, ctx, '{"success": true, "data": "ok"}')
    await hook.on_end(ctx, agent=None, output="done")  # type: ignore[arg-type]

    # min_tool_calls=2 → distiller still fires, but NOT the refiner.
    assert len(calls) == 1
    assert getattr(calls[0]["agent"], "name", None) == "skill-distiller"

    stats = skill_stats.read("happy")
    assert stats is not None
    assert stats["success_count"] == 1
    assert stats["error_count"] == 0


@pytest.mark.asyncio
async def test_refiner_does_not_fire_on_failure_without_skill_use(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    calls = _stub_runner(monkeypatch)
    hook = EvolutionHook(min_tool_calls=2)
    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "tricky"}])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _tool_error(hook, ctx, "Traceback (most recent call last): boom")
    await _tool_error(hook, ctx, "still failing")
    await hook.on_end(ctx, agent=None, output="gave up")  # type: ignore[arg-type]

    # No viewed skill → distiller path; refiner must not appear.
    names = [getattr(c["agent"], "name", None) for c in calls]
    assert "skill-refiner" not in names


@pytest.mark.asyncio
async def test_refiner_picks_most_recent_viewed_skill(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_skill(litehorse_home, "first")
    _seed_skill(litehorse_home, "second")
    calls = _stub_runner(monkeypatch)
    hook = EvolutionHook(min_tool_calls=2)
    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "chained"}])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _view_skill(hook, ctx, "first")
    await _view_skill(hook, ctx, "second")
    await _tool_error(hook, ctx, '{"success": false, "error": "nope"}')
    await hook.on_end(ctx, agent=None, output="broke")  # type: ignore[arg-type]

    payload = json.loads(calls[0]["prompt"])
    assert payload["skill_name"] == "second"

    # Both viewed skills record an error.
    assert skill_stats.read("first")["error_count"] == 1  # type: ignore[index]
    assert skill_stats.read("second")["error_count"] == 1  # type: ignore[index]


@pytest.mark.asyncio
async def test_refiner_exceptions_are_swallowed(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_skill(litehorse_home, "probe")

    async def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("refiner exploded")

    monkeypatch.setattr(evo_mod.Runner, "run", boom)
    hook = EvolutionHook(min_tool_calls=2)
    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "x"}])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _view_skill(hook, ctx, "probe")
    await _tool_error(hook, ctx, '{"success": false}')
    # Must not raise.
    await hook.on_end(ctx, agent=None, output="bad")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_distiller_suppressed_when_refiner_fires(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure with a viewed skill routes to refiner only — no duplicate call."""
    _seed_skill(litehorse_home, "probe")
    calls = _stub_runner(monkeypatch)
    hook = EvolutionHook(min_tool_calls=2)
    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "use probe"}])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _view_skill(hook, ctx, "probe")
    await _tool_error(hook, ctx, '{"success": false, "error": "x"}')
    await _tool_error(hook, ctx, '{"success": false, "error": "still x"}')
    await hook.on_end(ctx, agent=None, output="broke")  # type: ignore[arg-type]

    names = [getattr(c["agent"], "name", None) for c in calls]
    assert names == ["skill-refiner"]


@pytest.mark.asyncio
async def test_skill_view_failure_is_not_tracked(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``skill_view`` call that failed must NOT populate viewed_skills."""
    del litehorse_home
    calls = _stub_runner(monkeypatch)
    hook = EvolutionHook(min_tool_calls=2)
    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "try"}])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    # skill_view with success:false — should not register as a viewed skill
    await hook.on_tool_end(
        ctx,  # type: ignore[arg-type]
        agent=None,  # type: ignore[arg-type]
        tool=_NamedTool("skill_view"),  # type: ignore[arg-type]
        result=json.dumps({"success": False, "error": "not found"}),
    )
    await _tool_error(hook, ctx, '{"success": false, "error": "boom"}')
    await hook.on_end(ctx, agent=None, output="bad")  # type: ignore[arg-type]

    names = [getattr(c["agent"], "name", None) for c in calls]
    assert "skill-refiner" not in names
