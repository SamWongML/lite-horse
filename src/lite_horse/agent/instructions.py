"""Dynamic system prompt assembly.

Cache-stable block order:
SOUL persona → MEMORY snapshot → USER PROFILE snapshot → skills index →
AGENTS.md → tool guidance → Current time.

The time block is last so the volatile ``Current time:`` line does not
invalidate the OpenAI prompt cache on every turn — only the trailing bytes
change, and cache matching keys on the prefix.

The SDK evaluates ``instructions`` once per ``Runner.run``, so each of these
reads is a frozen snapshot for the duration of the run. Writes that happen
during the run land on disk but won't enter the prompt until the next run.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from agents import Agent, RunContextWrapper

from lite_horse.constants import litehorse_home
from lite_horse.memory.store import MemoryStore
from lite_horse.skills import stats as skill_stats

_SKILLS_INDEX_HEADER = "AVAILABLE SKILLS (load with skill_view)"
_FRAGILE_MIN_ERRORS = 3
_FRAGILE_MAX_SUCCESS_RATIO = 0.5
_FRAGILE_TAG = " (fragile — see stats)"

_TOOL_GUIDANCE = (
    "TOOL USE GUIDANCE:\n"
    "- Use `memory(action=...)` proactively when you learn something the human will "
    "benefit from remembering across sessions. Two targets: 'memory' for environment "
    "facts, conventions, lessons; 'user' for the human's preferences and identity.\n"
    "- Use `session_search(query=...)` BEFORE asking the human about something they "
    "may have already told you. It searches all past conversations.\n"
    "- Use `skill_view(name)` to load a skill before doing related work; use "
    "`skill_manage(action='create', ...)` AFTER completing a non-trivial task that "
    "is likely to recur."
)


def _read_optional(path: Path, max_chars: int = 8_000) -> str:
    try:
        return path.read_text(encoding="utf-8")[:max_chars].strip()
    except (FileNotFoundError, OSError):
        return ""


def _skill_description(skill_md: Path) -> str:
    """Cheap frontmatter parse — we only need the description line."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    for line in text.splitlines()[:80]:
        if line.lower().startswith("description:"):
            return line.split(":", 1)[1].strip()
    return ""


def _skills_index() -> str:
    """Render a stable, alphabetised index of bundled + user skills."""
    skills_dir = litehorse_home() / "skills"
    if not skills_dir.is_dir():
        return ""
    lines: list[str] = []
    for p in sorted(skills_dir.iterdir()):
        if not p.is_dir():
            continue
        skill_md = p / "SKILL.md"
        if not skill_md.is_file():
            continue
        desc = _skill_description(skill_md)
        suffix = _fragile_suffix(p.name)
        lines.append(f"- **{p.name}**: {desc or '(no description)'}{suffix}")
    if not lines:
        return ""
    return f"{_SKILLS_INDEX_HEADER}\n" + "\n".join(lines)


def _fragile_suffix(name: str) -> str:
    data = skill_stats.read(name)
    if not data:
        return ""
    errors = int(data.get("error_count", 0) or 0)
    successes = int(data.get("success_count", 0) or 0)
    uses = int(data.get("usage_count", 0) or 0)
    if errors < _FRAGILE_MIN_ERRORS or uses <= 0:
        return ""
    if successes / max(1, uses) >= _FRAGILE_MAX_SUCCESS_RATIO:
        return ""
    return _FRAGILE_TAG


InstructionsFn = Callable[[RunContextWrapper[Any], Agent[Any]], Awaitable[str]]


def make_instructions() -> InstructionsFn:
    """Return an async callable suitable for ``Agent(instructions=...)``."""

    async def _instructions(
        ctx: RunContextWrapper[Any], agent: Agent[Any]
    ) -> str:
        del ctx, agent
        home = litehorse_home()
        soul = _read_optional(home / "soul.md")
        agents_md = _read_optional(home / "AGENTS.md", max_chars=4_000)
        mem_block = MemoryStore.for_memory().render_block()
        usr_block = MemoryStore.for_user().render_block()
        skills_index = _skills_index()
        now = datetime.now().astimezone().isoformat(timespec="seconds")

        parts: list[str] = []
        if soul:
            parts.append(soul)
        if mem_block:
            parts.append(mem_block)
        if usr_block:
            parts.append(usr_block)
        if skills_index:
            parts.append(skills_index)
        if agents_md:
            parts.append("PROJECT CONTEXT (AGENTS.md):\n" + agents_md)
        parts.append(_TOOL_GUIDANCE)
        parts.append(f"Current time: {now}")
        return "\n\n".join(parts)

    return _instructions
