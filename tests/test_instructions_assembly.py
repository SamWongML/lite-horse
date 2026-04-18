"""Tests for the dynamic instructions assembler (Phase 6)."""
from __future__ import annotations

from pathlib import Path

import pytest

from hermes_lite.agent.instructions import make_instructions
from hermes_lite.memory.store import MemoryStore


@pytest.mark.asyncio
async def test_empty_state_has_time_and_tool_guidance(hermeslite_home: Path) -> None:
    del hermeslite_home
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "Current time:" in prompt
    assert "TOOL USE GUIDANCE:" in prompt
    assert "MEMORY [" not in prompt
    assert "USER PROFILE [" not in prompt
    assert "AVAILABLE SKILLS" not in prompt


@pytest.mark.asyncio
async def test_memory_block_appears_after_add(hermeslite_home: Path) -> None:
    del hermeslite_home
    MemoryStore.for_memory().add("user prefers terse commit messages")
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "MEMORY [" in prompt
    assert "user prefers terse commit messages" in prompt


@pytest.mark.asyncio
async def test_user_profile_block_appears_after_add(hermeslite_home: Path) -> None:
    del hermeslite_home
    MemoryStore.for_user().add("Name: Demon")
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "USER PROFILE [" in prompt
    assert "Name: Demon" in prompt


@pytest.mark.asyncio
async def test_skill_description_renders_in_index(hermeslite_home: Path) -> None:
    skill_dir = hermeslite_home / "skills" / "test"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test\ndescription: hello\n---\n\n# body\n",
        encoding="utf-8",
    )
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "AVAILABLE SKILLS" in prompt
    assert "**test**: hello" in prompt


@pytest.mark.asyncio
async def test_skill_without_description_falls_back(hermeslite_home: Path) -> None:
    skill_dir = hermeslite_home / "skills" / "nodesc"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# just a body\n", encoding="utf-8")
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "**nodesc**: (no description)" in prompt


@pytest.mark.asyncio
async def test_soul_and_agents_md_render(hermeslite_home: Path) -> None:
    (hermeslite_home / "soul.md").write_text(
        "You are Hermes-Lite, a calm assistant.", encoding="utf-8"
    )
    (hermeslite_home / "AGENTS.md").write_text(
        "Project: lite-horse.\nStyle: concise.", encoding="utf-8"
    )
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "You are Hermes-Lite" in prompt
    assert "PROJECT CONTEXT (AGENTS.md):" in prompt
    assert "Style: concise" in prompt


@pytest.mark.asyncio
async def test_block_ordering_is_stable(hermeslite_home: Path) -> None:
    """SOUL → time → MEMORY → USER PROFILE → SKILLS → AGENTS.md → tool guidance."""
    (hermeslite_home / "soul.md").write_text("SOUL_MARK", encoding="utf-8")
    MemoryStore.for_memory().add("MEM_MARK_entry")
    MemoryStore.for_user().add("USR_MARK_entry")
    skill_dir = hermeslite_home / "skills" / "probe"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: probe\ndescription: SKILL_MARK\n---\n", encoding="utf-8"
    )
    (hermeslite_home / "AGENTS.md").write_text("AGENTS_MARK", encoding="utf-8")

    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]

    positions = [
        prompt.index("SOUL_MARK"),
        prompt.index("Current time:"),
        prompt.index("MEM_MARK_entry"),
        prompt.index("USR_MARK_entry"),
        prompt.index("SKILL_MARK"),
        prompt.index("AGENTS_MARK"),
        prompt.index("TOOL USE GUIDANCE:"),
    ]
    assert positions == sorted(positions), f"block order violated: {positions}"
