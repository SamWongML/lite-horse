"""``make_instructions_for_user`` injects the session blocks."""
from __future__ import annotations

import pytest

from lite_horse.agent.instructions import (
    SessionSummaryBlock,
    make_instructions_for_user,
)
from lite_horse.effective import EffectiveConfig


def _empty_eff() -> EffectiveConfig:
    return EffectiveConfig.build(
        instructions=[],
        skills=[],
        commands=[],
        mcp_servers=[],
    )


@pytest.mark.asyncio
async def test_recent_sessions_block_renders() -> None:
    fn = make_instructions_for_user(
        _empty_eff(),
        memory_text="",
        user_md_text="",
        recent_sessions=[
            SessionSummaryBlock(topic="Q3 plan", summary="user shipped v0.5."),
            SessionSummaryBlock(topic="Refactor", summary="agent renamed the repo."),
        ],
    )
    prompt = await fn(None, None)  # type: ignore[arg-type]
    assert "## Recent Sessions" in prompt
    assert "Q3 plan" in prompt
    assert "user shipped v0.5." in prompt
    assert "Refactor" in prompt


@pytest.mark.asyncio
async def test_relevant_past_sessions_block_renders() -> None:
    fn = make_instructions_for_user(
        _empty_eff(),
        memory_text="",
        user_md_text="",
        relevant_sessions=[
            SessionSummaryBlock(topic="deploy", summary="discussed deploy plan"),
        ],
    )
    prompt = await fn(None, None)  # type: ignore[arg-type]
    assert "## Relevant Past Sessions" in prompt
    assert "deploy" in prompt
    assert "discussed deploy plan" in prompt


@pytest.mark.asyncio
async def test_empty_lists_emit_no_blocks() -> None:
    fn = make_instructions_for_user(
        _empty_eff(),
        memory_text="",
        user_md_text="",
        recent_sessions=[],
        relevant_sessions=[],
    )
    prompt = await fn(None, None)  # type: ignore[arg-type]
    assert "## Recent Sessions" not in prompt
    assert "## Relevant Past Sessions" not in prompt


@pytest.mark.asyncio
async def test_block_ordering_recent_before_relevant() -> None:
    fn = make_instructions_for_user(
        _empty_eff(),
        memory_text="",
        user_md_text="",
        recent_sessions=[
            SessionSummaryBlock(topic="RECENT_MARK", summary="x"),
        ],
        relevant_sessions=[
            SessionSummaryBlock(topic="RELEVANT_MARK", summary="y"),
        ],
    )
    prompt = await fn(None, None)  # type: ignore[arg-type]
    assert prompt.index("RECENT_MARK") < prompt.index("RELEVANT_MARK")


@pytest.mark.asyncio
async def test_empty_summary_entries_are_dropped() -> None:
    fn = make_instructions_for_user(
        _empty_eff(),
        memory_text="",
        user_md_text="",
        recent_sessions=[
            SessionSummaryBlock(topic="", summary=""),
        ],
    )
    prompt = await fn(None, None)  # type: ignore[arg-type]
    assert "## Recent Sessions" not in prompt


@pytest.mark.asyncio
async def test_block_appears_after_memory_before_skills() -> None:
    fn = make_instructions_for_user(
        _empty_eff(),
        memory_text="MEM_MARK_entry",
        user_md_text="USR_MARK_entry",
        recent_sessions=[
            SessionSummaryBlock(topic="RECENT", summary="content"),
        ],
    )
    prompt = await fn(None, None)  # type: ignore[arg-type]
    assert prompt.index("MEM_MARK_entry") < prompt.index("RECENT")
    assert prompt.index("RECENT") < prompt.index("TOOL USE GUIDANCE:")
