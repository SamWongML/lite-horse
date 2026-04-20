"""Tests for the dynamic instructions assembler (Phase 6)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lite_horse.agent.instructions import make_instructions
from lite_horse.memory.store import MemoryStore


@pytest.mark.asyncio
async def test_empty_state_has_time_and_tool_guidance(litehorse_home: Path) -> None:
    del litehorse_home
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "Current time:" in prompt
    assert "TOOL USE GUIDANCE:" in prompt
    assert "MEMORY [" not in prompt
    assert "USER PROFILE [" not in prompt
    assert "AVAILABLE SKILLS" not in prompt


@pytest.mark.asyncio
async def test_memory_block_appears_after_add(litehorse_home: Path) -> None:
    del litehorse_home
    MemoryStore.for_memory().add("user prefers terse commit messages")
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "MEMORY [" in prompt
    assert "user prefers terse commit messages" in prompt


@pytest.mark.asyncio
async def test_user_profile_block_appears_after_add(litehorse_home: Path) -> None:
    del litehorse_home
    MemoryStore.for_user().add("Name: Demon")
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "USER PROFILE [" in prompt
    assert "Name: Demon" in prompt


@pytest.mark.asyncio
async def test_skill_description_renders_in_index(litehorse_home: Path) -> None:
    skill_dir = litehorse_home / "skills" / "test"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test\ndescription: hello\n---\n\n# body\n",
        encoding="utf-8",
    )
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "AVAILABLE SKILLS" in prompt
    assert "**test**: hello" in prompt


@pytest.mark.asyncio
async def test_skill_without_description_falls_back(litehorse_home: Path) -> None:
    skill_dir = litehorse_home / "skills" / "nodesc"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# just a body\n", encoding="utf-8")
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "**nodesc**: (no description)" in prompt


@pytest.mark.asyncio
async def test_soul_and_agents_md_render(litehorse_home: Path) -> None:
    (litehorse_home / "soul.md").write_text(
        "You are Lite-Horse, a calm assistant.", encoding="utf-8"
    )
    (litehorse_home / "AGENTS.md").write_text(
        "Project: lite-horse.\nStyle: concise.", encoding="utf-8"
    )
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "You are Lite-Horse" in prompt
    assert "PROJECT CONTEXT (AGENTS.md):" in prompt
    assert "Style: concise" in prompt


@pytest.mark.asyncio
async def test_block_ordering_is_stable(litehorse_home: Path) -> None:
    """SOUL → MEMORY → USER PROFILE → SKILLS → AGENTS.md → tool guidance → time.

    Current time is last so it does not bust the OpenAI prompt cache every turn.
    """
    (litehorse_home / "soul.md").write_text("SOUL_MARK", encoding="utf-8")
    MemoryStore.for_memory().add("MEM_MARK_entry")
    MemoryStore.for_user().add("USR_MARK_entry")
    skill_dir = litehorse_home / "skills" / "probe"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: probe\ndescription: SKILL_MARK\n---\n", encoding="utf-8"
    )
    (litehorse_home / "AGENTS.md").write_text("AGENTS_MARK", encoding="utf-8")

    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]

    positions = [
        prompt.index("SOUL_MARK"),
        prompt.index("MEM_MARK_entry"),
        prompt.index("USR_MARK_entry"),
        prompt.index("SKILL_MARK"),
        prompt.index("AGENTS_MARK"),
        prompt.index("TOOL USE GUIDANCE:"),
        prompt.index("Current time:"),
    ]
    assert positions == sorted(positions), f"block order violated: {positions}"


@pytest.mark.asyncio
async def test_current_time_is_last_for_prompt_cache(litehorse_home: Path) -> None:
    """The volatile ``Current time:`` line must come AFTER ``TOOL USE GUIDANCE:``."""
    del litehorse_home
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert prompt.index("TOOL USE GUIDANCE:") < prompt.index("Current time:")
