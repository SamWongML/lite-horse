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


class _FakeCtx:
    """Minimal stand-in for ``RunContextWrapper`` — only ``turn_input`` is read."""

    def __init__(self, user_text: str) -> None:
        self.turn_input = [{"role": "user", "content": user_text}]


def _write_skill(home: Path, name: str, *, description: str, activate_when: str | None) -> None:
    skill_dir = home / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}"]
    if activate_when is not None:
        lines.append(activate_when.rstrip())
    lines.append("---")
    lines.append(f"\n# {name}\n")
    (skill_dir / "SKILL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_conditional_activation_includes_matching_skill(
    litehorse_home: Path,
) -> None:
    _write_skill(
        litehorse_home,
        "devops",
        description="ship a release",
        activate_when='activate_when:\n  - keywords: ["deploy", "ship"]',
    )
    _write_skill(
        litehorse_home,
        "poetry",
        description="writes verse",
        activate_when='activate_when:\n  - keywords: ["poem", "haiku"]',
    )
    prompt = await make_instructions()(
        _FakeCtx("please deploy the app to staging"), None  # type: ignore[arg-type]
    )
    assert "**devops**" in prompt
    assert "**poetry**" not in prompt


@pytest.mark.asyncio
async def test_conditional_activation_excludes_unmatched_skill(
    litehorse_home: Path,
) -> None:
    _write_skill(
        litehorse_home,
        "devops",
        description="ship a release",
        activate_when='activate_when:\n  - keywords: ["deploy", "ship"]',
    )
    _write_skill(
        litehorse_home,
        "poetry",
        description="writes verse",
        activate_when='activate_when:\n  - keywords: ["poem", "haiku"]',
    )
    prompt = await make_instructions()(
        _FakeCtx("write a poem about spring"), None  # type: ignore[arg-type]
    )
    assert "**poetry**" in prompt
    assert "**devops**" not in prompt


@pytest.mark.asyncio
async def test_fallback_includes_all_skills_when_no_turn_input(
    litehorse_home: Path,
) -> None:
    """ctx=None means extraction fails → all skills render."""
    _write_skill(
        litehorse_home,
        "devops",
        description="ship a release",
        activate_when='activate_when:\n  - keywords: ["deploy"]',
    )
    _write_skill(
        litehorse_home,
        "plan",
        description="planning skill",
        activate_when=None,
    )
    prompt = await make_instructions()(None, None)  # type: ignore[arg-type]
    assert "**devops**" in prompt
    assert "**plan**" in prompt


@pytest.mark.asyncio
async def test_top_k_cap_bounds_prompt_index(litehorse_home: Path) -> None:
    """25 activatable skills in the library → prompt lists at most 8."""
    for i in range(25):
        _write_skill(
            litehorse_home,
            f"helper{i:02d}",
            description=f"helper {i}",
            activate_when='activate_when:\n  - keywords: ["deploy"]',
        )
    prompt = await make_instructions()(
        _FakeCtx("please deploy it"), None  # type: ignore[arg-type]
    )
    rendered = sum(1 for i in range(25) if f"**helper{i:02d}**" in prompt)
    assert rendered <= 8
    assert rendered > 0
