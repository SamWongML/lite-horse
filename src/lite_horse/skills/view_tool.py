"""``skill_view`` @function_tool — Level-1 progressive disclosure.

The skills index in the system prompt lists only name + description. When
the agent decides a skill is relevant to the current task, it calls this
tool to pull the full SKILL.md body into its context on demand.

Phase 40: the tool body delegates to ``ctx.context.skill.view(slug)``
(the per-tenant :class:`SkillBackend`); cloud calls land on
:class:`SkillRepo` and CLI calls hit the local skills tree.
"""
from __future__ import annotations

import json
from typing import Any

from agents import RunContextWrapper, function_tool

from lite_horse.agent.backends import resolve_tenant
from lite_horse.skills.local_view import _VIEW_MAX_BYTES, _view

__all__ = ["_VIEW_MAX_BYTES", "_view", "skill_view"]


@function_tool(
    name_override="skill_view",
    description_override=(
        "Load the full SKILL.md for <name>. Call this after seeing the skill in "
        "the AVAILABLE SKILLS index and before doing related work. Returns the "
        "full markdown body plus frontmatter."
    ),
)
async def skill_view(ctx: RunContextWrapper[Any], name: str) -> str:
    backend = resolve_tenant(ctx).skill
    return json.dumps(await backend.view(name))
