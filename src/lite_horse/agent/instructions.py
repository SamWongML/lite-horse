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
from lite_horse.effective import EffectiveConfig
from lite_horse.memory.store import MemoryStore
from lite_horse.skills import stats as skill_stats
from lite_horse.skills.activation import filter_for_turn, filter_resolved_for_turn

_SKILLS_INDEX_HEADER = "AVAILABLE SKILLS (load with skill_view)"
_FRAGILE_MIN_ERRORS = 3
_FRAGILE_MAX_SUCCESS_RATIO = 0.5
_FRAGILE_TAG = " (fragile — see stats)"
_USER_REQUEST_MAX_CHARS = 500

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


def _skills_index(user_text: str | None = None) -> str:
    """Render a stable, alphabetised index of relevant skills for this turn.

    Phase 21: ``user_text`` drives conditional activation. When None, the
    filter returns every skill (fallback); otherwise the top-K most relevant
    are selected and re-sorted alphabetically for prompt stability.
    """
    skills_dir = litehorse_home() / "skills"
    entries = filter_for_turn(skills_dir=skills_dir, user_text=user_text)
    if not entries:
        return ""
    lines: list[str] = []
    for entry in sorted(entries, key=lambda e: e.name):
        desc = entry.description or "(no description)"
        suffix = _fragile_suffix(entry.name)
        lines.append(f"- **{entry.name}**: {desc}{suffix}")
    return f"{_SKILLS_INDEX_HEADER}\n" + "\n".join(lines)


def _extract_user_request(  # noqa: PLR0911 — branches are the shape
    ctx: RunContextWrapper[Any] | None,
) -> str | None:
    """Best-effort scrape of the latest user message from ``ctx.turn_input``.

    Mirrors :meth:`EvolutionHook._extract_user_request` — kept local so the
    instructions module has no dependency on the evolution hook.
    """
    if ctx is None:
        return None
    try:
        items = getattr(ctx, "turn_input", None)
        if not isinstance(items, list):
            return None
        for item in reversed(items):
            if isinstance(item, dict) and item.get("role") == "user":
                content = item.get("content")
                if isinstance(content, str):
                    return content[:_USER_REQUEST_MAX_CHARS]
                if isinstance(content, list):
                    parts = [
                        p.get("text", "")
                        for p in content
                        if isinstance(p, dict)
                        and p.get("type") in {"text", "input_text"}
                    ]
                    joined = "".join(parts)
                    return joined[:_USER_REQUEST_MAX_CHARS] if joined else None
            elif isinstance(item, str):
                return item[:_USER_REQUEST_MAX_CHARS]
    except Exception:
        return None
    return None


InstructionsFn = Callable[[RunContextWrapper[Any], Agent[Any]], Awaitable[str]]


def make_instructions() -> InstructionsFn:
    """Return an async callable suitable for ``Agent(instructions=...)``."""

    async def _instructions(
        ctx: RunContextWrapper[Any], agent: Agent[Any]
    ) -> str:
        del agent
        home = litehorse_home()
        soul = _read_optional(home / "soul.md")
        agents_md = _read_optional(home / "AGENTS.md", max_chars=4_000)
        mem_block = MemoryStore.for_memory().render_block()
        usr_block = MemoryStore.for_user().render_block()
        skills_index = _skills_index(_extract_user_request(ctx))
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


# ---------- v0.4 cloud path ----------


def make_instructions_for_user(
    eff: EffectiveConfig,
    *,
    memory_text: str,
    user_md_text: str,
) -> InstructionsFn:
    """Build the per-turn system prompt from a resolved EffectiveConfig.

    Block order (cache-stable prefix, volatile suffix):

    1. Bundled+official instructions, in (priority, slug) order.
    2. ``user.md`` profile block.
    3. ``memory.md`` snapshot.
    4. Skills index (activated subset).
    5. Tool guidance.
    6. Current time (last so cache-prefix bytes don't change per turn).

    The skills index is the only block that varies with ``ctx`` (it
    re-scores against the latest user message); everything else is
    fixed at agent-build time so the OpenAI prompt cache hits cleanly
    on subsequent turns of the same session.
    """

    instruction_blocks = [
        i.body.strip() for i in eff.instructions if i.body.strip()
    ]
    profile_block = _render_block("USER PROFILE", user_md_text)
    memory_block = _render_block("MEMORY", memory_text)

    async def _instructions(
        ctx: RunContextWrapper[Any], agent: Agent[Any]
    ) -> str:
        del agent
        user_text = _extract_user_request(ctx)
        skills_index = _resolved_skills_index(
            eff, user_text=user_text, user_profile_text=user_md_text
        )
        now = datetime.now().astimezone().isoformat(timespec="seconds")

        parts: list[str] = []
        parts.extend(instruction_blocks)
        if profile_block:
            parts.append(profile_block)
        if memory_block:
            parts.append(memory_block)
        if skills_index:
            parts.append(skills_index)
        parts.append(_TOOL_GUIDANCE)
        parts.append(f"Current time: {now}")
        return "\n\n".join(parts)

    return _instructions


def _render_block(header: str, body: str) -> str:
    body = body.strip()
    if not body:
        return ""
    return f"{header}:\n{body}"


def _resolved_skills_index(
    eff: EffectiveConfig,
    *,
    user_text: str | None,
    user_profile_text: str,
) -> str:
    selected = filter_resolved_for_turn(
        eff.skills, user_text=user_text, user_profile_text=user_profile_text
    )
    if not selected:
        return ""
    lines = []
    for skill in sorted(selected, key=lambda s: s.slug):
        desc = skill.description or "(no description)"
        lines.append(f"- **{skill.slug}**: {desc}")
    return f"{_SKILLS_INDEX_HEADER}\n" + "\n".join(lines)
