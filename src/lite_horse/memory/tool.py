"""``memory`` @function_tool — agent-facing add/replace/remove for MEMORY.md and USER.md.

Every read/write routes through the per-turn :class:`TenantContext.memory`
backend on ``RunContextWrapper.context``. Cloud calls land in Postgres via
``MemoryRepo``; CLI calls land on the local filesystem via
:class:`MemoryStore` — both behind the same ``MemoryBackend`` Protocol so
the JSON wire shape is identical.

A best-effort recall re-index runs on top of every successful write so
the ``memory_search`` tool can surface freshly-stored entries the next
time the agent asks. Indexing failures are swallowed — the caller's
write already succeeded and recall is auxiliary.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

from agents import RunContextWrapper, function_tool

from lite_horse.agent.backends import (
    MemoryFull,
    TenantContext,
    UnsafeMemoryContent,
    resolve_tenant,
)

_log = logging.getLogger(__name__)


async def _reindex_memory_doc(
    tenant: TenantContext, kind: Literal["memory", "user"]
) -> None:
    """Re-embed the whole memory doc after a successful mutation."""
    try:
        body = await tenant.memory.get(kind)
        await tenant.recall.index(
            source_kind="memory_md" if kind == "memory" else "user_md",
            source_id=None,
            content=body,
        )
    except Exception:
        _log.warning("memory: recall.index failed (kind=%s)", kind)


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
    tenant = resolve_tenant(ctx)
    backend = tenant.memory
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
            await _reindex_memory_doc(tenant, kind)
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
            await _reindex_memory_doc(tenant, kind)
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
            await _reindex_memory_doc(tenant, kind)
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
