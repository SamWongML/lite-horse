"""Tests for the iteration-budget pressure hook (Phase 5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from lite_horse.agent.budget import BudgetHook


@dataclass
class _FakeCtx:
    turn_input: list[Any] = field(default_factory=list)


class _Sentinel:
    """Marker tool value — the hook ignores it."""


def _tool_item(content: str = "result") -> dict[str, Any]:
    return {"role": "tool", "content": content}


async def _tick(
    hook: BudgetHook, ctx: _FakeCtx, n: int, *, ensure_tool_item: bool = True
) -> None:
    for _ in range(n):
        if ensure_tool_item:
            ctx.turn_input = [_tool_item()]
        await hook.on_tool_end(ctx, agent=None, tool=_Sentinel(), result="ok")  # type: ignore[arg-type]


def test_tier_for_below_caution_returns_none() -> None:
    hook = BudgetHook(max_turns=100)
    assert hook._tier_for(0.5) is None
    assert hook._tier_for(0.69) is None


def test_tier_for_caution_band() -> None:
    hook = BudgetHook(max_turns=100)
    assert hook._tier_for(0.70) == "caution"
    assert hook._tier_for(0.75) == "caution"
    assert hook._tier_for(0.89) == "caution"


def test_tier_for_warning_band() -> None:
    hook = BudgetHook(max_turns=100)
    assert hook._tier_for(0.90) == "warning"
    assert hook._tier_for(0.92) == "warning"
    assert hook._tier_for(1.5) == "warning"


@pytest.mark.asyncio
async def test_caution_note_appended_after_crossing_threshold() -> None:
    hook = BudgetHook(max_turns=10)
    ctx = _FakeCtx(turn_input=[_tool_item("initial")])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]

    # 6 tool calls = 60% — below caution, no note.
    for _ in range(6):
        ctx.turn_input = [_tool_item("step")]
        await hook.on_tool_end(ctx, agent=None, tool=_Sentinel(), result="ok")  # type: ignore[arg-type]
    assert "BUDGET" not in ctx.turn_input[-1]["content"]

    # 7th tool call = 70% → caution note injected on that item.
    ctx.turn_input = [_tool_item("step7")]
    await hook.on_tool_end(ctx, agent=None, tool=_Sentinel(), result="ok")  # type: ignore[arg-type]
    content = ctx.turn_input[-1]["content"]
    assert "BUDGET" in content
    assert "WARNING" not in content  # still caution, not warning
    assert "7/10" in content


@pytest.mark.asyncio
async def test_caution_note_appended_once_per_tier() -> None:
    hook = BudgetHook(max_turns=10)
    ctx = _FakeCtx()

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    # Tick up to 7 (caution).
    await _tick(hook, ctx, 7)
    first_content = ctx.turn_input[-1]["content"]
    assert first_content.count("[BUDGET") == 1

    # 8th tool call — still in caution tier; should NOT append again.
    ctx.turn_input = [_tool_item("step8")]
    await hook.on_tool_end(ctx, agent=None, tool=_Sentinel(), result="ok")  # type: ignore[arg-type]
    assert "[BUDGET" not in ctx.turn_input[-1]["content"]


@pytest.mark.asyncio
async def test_crosses_both_tiers_emits_caution_then_warning() -> None:
    hook = BudgetHook(max_turns=10)
    ctx = _FakeCtx()
    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]

    notes_seen: list[str] = []

    for i in range(1, 10):
        ctx.turn_input = [_tool_item(f"step{i}")]
        await hook.on_tool_end(ctx, agent=None, tool=_Sentinel(), result="ok")  # type: ignore[arg-type]
        content = ctx.turn_input[-1]["content"]
        if "[BUDGET" in content:
            notes_seen.append(content)

    assert len(notes_seen) == 2
    assert "WARNING" not in notes_seen[0]  # caution first
    assert "WARNING" in notes_seen[1]  # warning second


@pytest.mark.asyncio
async def test_on_start_resets_state_between_runs() -> None:
    hook = BudgetHook(max_turns=10)
    ctx = _FakeCtx()

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    await _tick(hook, ctx, 7)
    assert hook.iteration == 7
    assert hook._last_tier == "caution"

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    assert hook.iteration == 0
    assert hook._last_tier is None


@pytest.mark.asyncio
async def test_no_tool_item_in_turn_input_does_not_advance_tier() -> None:
    """If there's no tool-role item to mutate, the tier stays unset so a later
    tool result can still receive the note."""
    hook = BudgetHook(max_turns=10)
    ctx = _FakeCtx(turn_input=[{"role": "user", "content": "hi"}])

    await hook.on_start(ctx, agent=None)  # type: ignore[arg-type]
    # Cross 70% without any tool-role item present.
    for _ in range(7):
        await hook.on_tool_end(ctx, agent=None, tool=_Sentinel(), result="ok")  # type: ignore[arg-type]
    assert hook._last_tier is None

    # Next call: tool item present — caution should land now.
    ctx.turn_input = [_tool_item("step8")]
    await hook.on_tool_end(ctx, agent=None, tool=_Sentinel(), result="ok")  # type: ignore[arg-type]
    assert "[BUDGET" in ctx.turn_input[-1]["content"]
    assert hook._last_tier == "caution"
