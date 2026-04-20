"""``skill_manage`` @function_tool — agent-managed CRUD over ~/.litehorse/skills/.

The tool is intentionally a thin wrapper around :func:`dispatch`; tests target
the pure dispatch helper rather than the SDK-decorated callable.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Literal

from agents import RunContextWrapper, function_tool

from lite_horse.security.validators import UnsafeContent, check_untrusted
from lite_horse.skills._slug import _SLUG_RE
from lite_horse.skills.source import skills_root

Action = Literal[
    "create", "patch", "edit", "delete", "write_file", "remove_file", "list",
]


def _skill_dir(name: str) -> Path:
    if not _SLUG_RE.match(name):
        raise ValueError(
            f"invalid skill name {name!r}; must be lowercase, alphanumeric + dash/underscore, "
            "max 64 chars, start with [a-z0-9]"
        )
    return skills_root() / name


def _resolve_inside(root: Path, rel: str) -> Path:
    """Resolve ``rel`` against ``root`` and reject paths that escape it."""
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if not target.is_relative_to(root_resolved):
        raise ValueError("file_path escapes skill directory")
    return target


def dispatch(  # noqa: PLR0911, PLR0912, PLR0915 — branch-per-action; flat dispatch is the readable shape
    action: Action,
    *,
    name: str | None = None,
    content: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    file_path: str | None = None,
) -> dict[str, Any]:
    """Execute a skill-management action and return a JSON-serializable result."""
    if action == "list":
        return {
            "success": True,
            "skills": [p.name for p in sorted(skills_root().iterdir()) if p.is_dir()],
        }

    if not name:
        return {"success": False, "error": "name is required"}
    try:
        d = _skill_dir(name)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    skill_md = d / "SKILL.md"

    if action == "create":
        if d.exists():
            return {"success": False, "error": f"skill {name!r} already exists"}
        if not content or "---" not in content:
            return {
                "success": False,
                "error": (
                    "content must be a complete SKILL.md with YAML frontmatter "
                    "(--- name: ... description: ... ---)"
                ),
            }
        try:
            check_untrusted(content)
        except UnsafeContent as e:
            return {"success": False, "error": f"unsafe skill content: {e}"}
        d.mkdir(parents=True)
        skill_md.write_text(content, encoding="utf-8")
        return {"success": True, "path": f"skills/{name}/SKILL.md"}

    if action == "patch":
        if not skill_md.exists():
            return {"success": False, "error": f"skill {name!r} does not exist"}
        if not (old_string and new_string is not None):
            return {"success": False, "error": "old_string and new_string required"}
        text = skill_md.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            return {"success": False, "error": "old_string not found"}
        if count > 1:
            return {
                "success": False,
                "error": f"old_string matches {count} times; make it unique",
            }
        skill_md.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return {"success": True}

    if action == "edit":
        if not skill_md.exists():
            return {"success": False, "error": f"skill {name!r} does not exist"}
        if not content or "---" not in content:
            return {"success": False, "error": "content must include YAML frontmatter"}
        try:
            check_untrusted(content)
        except UnsafeContent as e:
            return {"success": False, "error": f"unsafe skill content: {e}"}
        skill_md.write_text(content, encoding="utf-8")
        return {"success": True}

    if action == "delete":
        if not d.exists():
            return {"success": False, "error": f"skill {name!r} does not exist"}
        shutil.rmtree(d)
        return {"success": True}

    if action == "write_file":
        if not (file_path and content is not None):
            return {"success": False, "error": "file_path and content required"}
        if not d.exists():
            return {"success": False, "error": f"skill {name!r} does not exist"}
        try:
            target = _resolve_inside(d, file_path)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        try:
            check_untrusted(content)
        except UnsafeContent as e:
            return {"success": False, "error": f"unsafe file content: {e}"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"success": True, "path": f"skills/{name}/{file_path}"}

    if action == "remove_file":
        if not file_path:
            return {"success": False, "error": "file_path required"}
        if not d.exists():
            return {"success": False, "error": f"skill {name!r} does not exist"}
        try:
            target = _resolve_inside(d, file_path)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        if target.exists():
            target.unlink()
        return {"success": True}

    return {"success": False, "error": f"unknown action {action!r}"}


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
async def skill_manage(
    ctx: RunContextWrapper[Any],
    action: Action,
    name: str | None = None,
    content: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    file_path: str | None = None,
) -> str:
    del ctx  # unused; the tool operates on the on-disk skills dir
    result = dispatch(
        action,
        name=name,
        content=content,
        old_string=old_string,
        new_string=new_string,
        file_path=file_path,
    )
    return json.dumps(result)
