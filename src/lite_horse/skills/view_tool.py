"""``skill_view`` @function_tool — Level-1 progressive disclosure.

The skills index in the system prompt lists only name + description. When the
agent decides a skill is relevant to the current task, it calls this tool to
pull the full SKILL.md body into its context on demand.
"""
from __future__ import annotations

import json
from typing import Any

from agents import RunContextWrapper, function_tool

from lite_horse.skills import stats as skill_stats
from lite_horse.skills._slug import _SLUG_RE
from lite_horse.skills.source import skills_root

# One byte above the 15 KB skill size cap so a legal skill always fits and an
# over-size one is truncated with a visible marker rather than silently cropped.
_VIEW_MAX_BYTES = 16 * 1024
_TRUNCATION_MARKER = "\n\n[... truncated by skill_view; full file on disk]"


def _view(name: str) -> dict[str, Any]:
    if not isinstance(name, str) or not _SLUG_RE.match(name):
        return {
            "success": False,
            "error": (
                f"invalid skill name {name!r}; must be lowercase, alphanumeric + "
                "dash/underscore, max 64 chars, start with [a-z0-9]"
            ),
        }
    root = skills_root().resolve()
    target = (root / name / "SKILL.md").resolve()
    if not target.is_relative_to(root):
        return {"success": False, "error": "path escapes skills directory"}
    if not target.is_file():
        return {"success": False, "error": f"skill {name!r} not found"}
    text = target.read_text(encoding="utf-8")
    if len(text.encode("utf-8")) > _VIEW_MAX_BYTES:
        encoded = text.encode("utf-8")[: _VIEW_MAX_BYTES - len(_TRUNCATION_MARKER)]
        text = encoded.decode("utf-8", errors="ignore") + _TRUNCATION_MARKER
    skill_stats.record_view(name)
    return {"success": True, "name": name, "content": text}


@function_tool(
    name_override="skill_view",
    description_override=(
        "Load the full SKILL.md for <name>. Call this after seeing the skill in "
        "the AVAILABLE SKILLS index and before doing related work. Returns the "
        "full markdown body plus frontmatter."
    ),
)
async def skill_view(ctx: RunContextWrapper[Any], name: str) -> str:
    del ctx
    return json.dumps(_view(name))
