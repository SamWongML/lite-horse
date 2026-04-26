"""Shared types for the sessions surface.

`SearchHit` is the row shape returned by both the local SQLite FTS5 path
(`sessions.local.LocalSessionRepo.search_messages`) and the Postgres
tsvector path (`repositories.message_repo.MessageRepo.search_messages`).
Lives in its own module so the repository layer can import it without
pulling in `sqlite3` or any local-only baggage.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchHit:
    id: int
    session_id: str
    role: str
    timestamp: float
    snippet: str
    source: str
