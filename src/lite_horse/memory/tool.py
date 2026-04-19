"""``memory`` @function_tool — agent-facing add/replace/remove for MEMORY.md and USER.md."""
from __future__ import annotations

import json
from typing import Any, Literal

from agents import RunContextWrapper, function_tool

from lite_horse.memory.store import MemoryFull, MemoryStore, UnsafeMemoryContent


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
    del ctx  # unused; the tool reads its target store from disk on each call
    store = MemoryStore.for_memory() if target == "memory" else MemoryStore.for_user()
    try:
        if action == "add":
            if not content:
                return json.dumps({"success": False, "error": "content is required for add"})
            store.add(content)
            return json.dumps(
                {"success": True, "usage": f"{store.total_chars()}/{store.char_limit}"}
            )
        if action == "replace":
            if not (old_text and content):
                return json.dumps(
                    {"success": False, "error": "old_text and content required for replace"}
                )
            store.replace(old_text, content)
            return json.dumps(
                {"success": True, "usage": f"{store.total_chars()}/{store.char_limit}"}
            )
        if action == "remove":
            if not old_text:
                return json.dumps({"success": False, "error": "old_text is required for remove"})
            store.remove(old_text)
            return json.dumps(
                {"success": True, "usage": f"{store.total_chars()}/{store.char_limit}"}
            )
    except MemoryFull as e:
        return json.dumps(
            {
                "success": False,
                "error": str(e),
                "current_entries": store.entries(),
                "usage": f"{e.current}/{e.limit}",
            }
        )
    except (ValueError, UnsafeMemoryContent) as e:
        return json.dumps({"success": False, "error": str(e)})
    return json.dumps({"success": False, "error": f"unknown action: {action!r}"})
