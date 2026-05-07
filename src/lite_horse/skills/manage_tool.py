"""``skill_manage`` @function_tool — agent-managed CRUD over skills.

Phase 40 made the tool body a thin async dispatcher routed through
:class:`TenantContext.skill` from ``RunContextWrapper.context``. Cloud
calls land in Postgres via :class:`SkillRepo`; CLI calls land on the
local filesystem via the legacy :func:`dispatch` helper, now hosted in
:mod:`lite_horse.skills.local_dispatch` so this file stays free of
``skills_root`` imports per the Phase 40 lint contract.

The ``dispatch`` symbol is re-exported here for backward compatibility
with existing tests; new code should call into the relevant
``SkillBackend`` impl directly.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from agents import RunContextWrapper, function_tool

from lite_horse.agent.backends import resolve_tenant
from lite_horse.skills.local_dispatch import Action, dispatch

__all__ = ["Action", "dispatch", "skill_manage"]


_ACTIONS: tuple[str, ...] = (
    "create",
    "patch",
    "edit",
    "delete",
    "write_file",
    "remove_file",
    "list",
)


@function_tool(
    name_override="skill_manage",
    description_override=(
        "Create, update, or delete your own skills. Skills are markdown documents "
        "you can load on demand. Use 'create' to write a new SKILL.md, 'patch' for "
        "targeted old_string→new_string edits (preferred), 'edit' for full "
        "rewrites, 'delete' to remove a skill, 'write_file'/'remove_file' for "
        "supporting files (references/, scripts/, templates/), and 'list' to "
        "enumerate existing skills. Skills you create are picked up on the next run."
    ),
)
async def skill_manage(  # noqa: PLR0911, PLR0912 — flat dispatch keeps the wire shape readable
    ctx: RunContextWrapper[Any],
    action: Literal[
        "create", "patch", "edit", "delete", "write_file", "remove_file", "list"
    ],
    name: str | None = None,
    content: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    file_path: str | None = None,
) -> str:
    backend = resolve_tenant(ctx).skill
    if action == "list":
        return json.dumps({"success": True, "skills": await backend.list_slugs()})
    if not name:
        return json.dumps({"success": False, "error": "name is required"})
    if action == "create":
        if not content:
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        "content must be a complete SKILL.md with YAML frontmatter "
                        "(--- name: ... description: ... ---)"
                    ),
                }
            )
        return json.dumps(await backend.create(slug=name, content=content))
    if action == "patch":
        if not (old_string and new_string is not None):
            return json.dumps(
                {"success": False, "error": "old_string and new_string required"}
            )
        return json.dumps(
            await backend.patch(
                slug=name, old_string=old_string, new_string=new_string
            )
        )
    if action == "edit":
        if not content:
            return json.dumps(
                {"success": False, "error": "content must include YAML frontmatter"}
            )
        return json.dumps(await backend.edit(slug=name, content=content))
    if action == "delete":
        return json.dumps(await backend.delete(slug=name))
    if action == "write_file":
        if not (file_path and content is not None):
            return json.dumps(
                {"success": False, "error": "file_path and content required"}
            )
        return json.dumps(
            await backend.write_file(
                slug=name, file_path=file_path, content=content
            )
        )
    if action == "remove_file":
        if not file_path:
            return json.dumps({"success": False, "error": "file_path required"})
        return json.dumps(
            await backend.remove_file(slug=name, file_path=file_path)
        )
    return json.dumps({"success": False, "error": f"unknown action {action!r}"})
