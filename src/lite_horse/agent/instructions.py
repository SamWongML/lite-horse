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

Phase 40: memory reads route through ``ctx.context.memory`` (a
:class:`MemoryBackend`) so cloud calls land in Postgres and CLI calls hit
the local FS — both via the same Protocol seam. The CLI skill index +
SOUL / AGENTS.md reads come through opt-in helpers from
:mod:`lite_horse.skills.activation` and :mod:`lite_horse.local_prompt`,
keeping this module free of ``litehorse_home`` / ``MemoryStore`` /
``skills_root`` imports per the Phase 40 lint contract.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from agents import Agent, RunContextWrapper

from lite_horse.agent.backends import MemoryBackend, resolve_tenant
from lite_horse.constants import ENTRY_DELIMITER
from lite_horse.effective import EffectiveConfig
from lite_horse.local_prompt import LocalPromptExtras, load_local_prompt_extras
from lite_horse.skills.activation import (
    filter_resolved_for_turn,
    render_local_skills_index_block,
)

_SKILLS_INDEX_HEADER = "AVAILABLE SKILLS (load with skill_view)"
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


InstructionsFn = Callable[
    [RunContextWrapper[Any], Agent[Any]], Awaitable[str]
]


def _default_extras_loader() -> LocalPromptExtras:
    return load_local_prompt_extras()


__all__ = [
    "InstructionsFn",
    "LocalPromptExtras",
    "make_instructions",
    "make_instructions_for_user",
]


async def _entries_to_block(
    backend: MemoryBackend, kind: Any, label: str
) -> str:
    """Render a memory document as the v0.3 system-prompt block."""
    try:
        entries = await backend.entries(kind)
    except Exception:
        return ""
    if not entries:
        return ""
    chars = sum(len(e) for e in entries) + max(0, len(entries) - 1) * len(
        ENTRY_DELIMITER
    )
    limit = backend.char_limit(kind)
    pct = round(chars / limit * 100) if limit else 0
    body = ENTRY_DELIMITER.join(entries)
    rule = "═" * 46
    return f"{rule}\n{label} [{pct}% — {chars}/{limit} chars]\n{rule}\n{body}"


def _skills_index(user_text: str | None = None) -> str:
    """Sync local-FS skills index. Used by the CLI prompt path + tests."""
    return render_local_skills_index_block(
        user_text=user_text, header=_SKILLS_INDEX_HEADER
    )


def _extract_user_request(  # noqa: PLR0911 — branches are the shape
    ctx: RunContextWrapper[Any] | None,
) -> str | None:
    """Best-effort scrape of the latest user message from ``ctx.turn_input``."""
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


def make_instructions(
    *,
    extras_loader: Callable[[], LocalPromptExtras] | None = None,
) -> InstructionsFn:
    """Return an async callable suitable for ``Agent(instructions=...)``.

    ``extras_loader`` injects the CLI-only SOUL / AGENTS.md reads. When
    ``None``, the loader from :mod:`lite_horse.local_prompt` is used so
    legacy tests calling ``make_instructions()`` keep observing the
    same prompt shape.
    """
    resolved_loader = extras_loader or _default_extras_loader

    async def _instructions(
        ctx: RunContextWrapper[Any], agent: Agent[Any]
    ) -> str:
        del agent
        tenant = resolve_tenant(ctx)
        try:
            extras = resolved_loader()
        except Exception:
            extras = LocalPromptExtras.empty()

        mem_block = await _entries_to_block(tenant.memory, "memory", "MEMORY")
        usr_block = await _entries_to_block(
            tenant.memory, "user", "USER PROFILE"
        )
        skills_index = _skills_index(_extract_user_request(ctx))
        now = datetime.now().astimezone().isoformat(timespec="seconds")

        parts: list[str] = []
        if extras.soul:
            parts.append(extras.soul)
        if mem_block:
            parts.append(mem_block)
        if usr_block:
            parts.append(usr_block)
        if skills_index:
            parts.append(skills_index)
        if extras.agents_md:
            parts.append("PROJECT CONTEXT (AGENTS.md):\n" + extras.agents_md)
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
