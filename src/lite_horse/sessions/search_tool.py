"""``session_search`` tool — FTS5 lookup across all past conversations."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from agents import RunContextWrapper, function_tool

from lite_horse.sessions.db import SessionDB

# Module-level singleton; the CLI / gateway / cron entrypoints wire this up
# once at startup via :func:`bind_db`. Tools are module-level callables so we
# cannot capture per-instance state any other way without threading a context
# through every tool invocation.
_DB: SessionDB | None = None


def bind_db(db: SessionDB) -> None:
    """Bind the :class:`SessionDB` the tool should query."""
    global _DB  # noqa: PLW0603 — single-user app, intentional singleton
    _DB = db


@function_tool(
    name_override="session_search",
    description_override=(
        "Search across all past conversations using full-text search (FTS5). "
        "Use FTS5 syntax: bare keywords for AND, \"quoted phrases\", "
        "OR/NOT operators, and prefix* matching. Returns up to 20 hits with "
        "session_id, role, timestamp, and a snippet containing >>>match<<< "
        "markers."
    ),
)
async def session_search(
    ctx: RunContextWrapper[Any],
    query: str,
    limit: int = 20,
    source: str | None = None,
    role: str | None = None,
) -> str:
    """FTS5 search over the session store. Returns a JSON array of hits."""
    del ctx  # unused; tool reads from the module-level DB singleton
    if _DB is None:
        return json.dumps({"error": "session DB not bound"})
    hits = _DB.search_messages(
        query,
        limit=min(max(1, int(limit)), 50),
        source_filter=[source] if source else None,
        role_filter=[role] if role else None,
    )
    return json.dumps([asdict(h) for h in hits])
