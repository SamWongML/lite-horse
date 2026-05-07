"""``memory`` @function_tool — agent-facing add/replace/remove for MEMORY.md and USER.md.

Phase 40 routes every read/write through the per-turn
:class:`TenantContext.memory` backend on ``RunContextWrapper.context``.
Cloud calls land in Postgres via ``MemoryRepo``; CLI calls land on the
local filesystem via :class:`MemoryStore` — both behind the same
``MemoryBackend`` Protocol so the JSON wire shape is identical.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from agents import RunContextWrapper, function_tool

from lite_horse.agent.backends import (
    MemoryFull,
    UnsafeMemoryContent,
    resolve_tenant,
)


@function_tool(
    name_override="memory",
    description_override=(
        "Manage persistent memory across sessions. Two targets: 'memory' (your "
        "personal notes about the environment, conventions, lessons) and 'user' "
        "(facts about the human's preferences, identity, communication style). "
        "Use action='add' to insert a new entry, 'replace' to update one (matched "
        "by substring), or 'remove' to delete one. There is no 'read' action — "
        "memory is automatically injected into your system prompt at session start."
    ),
)
async def memory_tool(  # noqa: PLR0911 — branch-per-action keeps the JSON shape inline
    ctx: RunContextWrapper[Any],
    action: Literal["add", "replace", "remove"],
    target: Literal["memory", "user"],
    content: str | None = None,
    old_text: str | None = None,
) -> str:
    backend = resolve_tenant(ctx).memory
    kind: Literal["memory", "user"] = (
        "memory" if target == "memory" else "user"
    )
    try:
        if action == "add":
            if not content:
                return json.dumps(
                    {"success": False, "error": "content is required for add"}
                )
            await backend.add(kind, content)
            total = await backend.total_chars(kind)
            return json.dumps(
                {"success": True, "usage": f"{total}/{backend.char_limit(kind)}"}
            )
        if action == "replace":
            if not (old_text and content):
                return json.dumps(
                    {
                        "success": False,
                        "error": "old_text and content required for replace",
                    }
                )
            await backend.replace(kind, old_text, content)
            total = await backend.total_chars(kind)
            return json.dumps(
                {"success": True, "usage": f"{total}/{backend.char_limit(kind)}"}
            )
        if action == "remove":
            if not old_text:
                return json.dumps(
                    {"success": False, "error": "old_text is required for remove"}
                )
            await backend.remove(kind, old_text)
            total = await backend.total_chars(kind)
            return json.dumps(
                {"success": True, "usage": f"{total}/{backend.char_limit(kind)}"}
            )
    except MemoryFull as e:
        entries = await backend.entries(kind)
        return json.dumps(
            {
                "success": False,
                "error": str(e),
                "current_entries": entries,
                "usage": f"{e.current}/{e.limit}",
            }
        )
    except (ValueError, UnsafeMemoryContent) as e:
        return json.dumps({"success": False, "error": str(e)})
    return json.dumps({"success": False, "error": f"unknown action: {action!r}"})
