"""``memory_search`` @function_tool — semantic recall over user history.

Lifts the wholesale-injection ceiling on the system prompt: instead of
folding every byte of memory.md into the prompt, the agent reaches for
``memory_search`` whenever it needs to recall something not visible in
MEMORY.md.

The tool routes through :class:`TenantContext.recall` so cloud calls land
in pgvector via :class:`MemoryChunkRepo` and CLI calls land in the local
sqlite recall store; the JSON wire shape is identical across both.
"""
from __future__ import annotations

import json
from typing import Any

from agents import RunContextWrapper, function_tool

from lite_horse.agent.backends import resolve_tenant

_DEFAULT_K = 5
_MAX_K = 20
_PREVIEW_CHARS = 480


@function_tool(
    name_override="memory_search",
    description_override=(
        "Semantically search the user's full memory + skill + session "
        "history. Use when you need to recall something that isn't in "
        "the MEMORY.md / USER.md blocks at the top of your prompt — the "
        "wholesale-injected blocks are short by design. Args: query "
        "(natural-language question), k (1-20, default 5). Results "
        "carry source_kind ('memory_md' | 'user_md' | 'session_summary' "
        "| 'message' | 'skill_body'), source_id (the underlying entity "
        "or null), score (higher is better), and content (truncated to "
        "~480 chars). If no rows return, the user has nothing matching."
    ),
)
async def memory_search(
    ctx: RunContextWrapper[Any],
    query: str,
    k: int = _DEFAULT_K,
) -> str:
    if not query or not query.strip():
        return json.dumps({"success": False, "error": "query is required"})
    bounded_k = max(1, min(int(k or _DEFAULT_K), _MAX_K))
    backend = resolve_tenant(ctx).recall
    try:
        rows = await backend.search(query, k=bounded_k)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})
    return json.dumps(
        {
            "success": True,
            "query": query,
            "results": [
                {
                    "source_kind": r.source_kind,
                    "source_id": r.source_id,
                    "score": round(r.score, 4),
                    "content": (
                        r.content[:_PREVIEW_CHARS] + "…"
                        if len(r.content) > _PREVIEW_CHARS
                        else r.content
                    ),
                    "ts": r.ts_iso,
                }
                for r in rows
            ],
        }
    )
