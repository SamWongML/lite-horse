"""``RecallBackend`` Protocol — semantic recall over the user's history.

Phase 42 lifts the 2 400-char wholesale-injection ceiling. Instead of
folding every byte of memory.md into the system prompt, the agent
queries this backend on demand via the ``memory_search`` tool. Two impls
ship next to this file:

* :mod:`recall_local` — chromadb on ``~/.litehorse/embeddings/`` for the
  CLI / single-user path.
* :mod:`recall_cloud` — pgvector + BM25 hybrid through
  :class:`MemoryChunkRepo` for the multi-tenant API path.

Indexing triggers (whole-doc re-embed on memory.md / user.md write,
single-row embed on skill body) live in the backend impls; tools call
:meth:`index` / :meth:`delete` whenever durable state changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

SourceKind = Literal[
    "memory_md", "user_md", "session_summary", "message", "skill_body"
]


@dataclass(frozen=True)
class Recalled:
    """One hit from :meth:`RecallBackend.search`.

    ``score`` is the blended cosine + BM25 score; ``source_kind`` /
    ``source_id`` let the caller dedupe and link back to the underlying
    entity.
    """

    source_kind: str
    source_id: str | None
    content: str
    score: float
    ts_iso: str


@runtime_checkable
class RecallBackend(Protocol):
    """Per-tenant semantic recall index."""

    async def index(
        self,
        *,
        source_kind: SourceKind,
        source_id: str | None,
        content: str,
    ) -> int:
        """Re-embed ``content`` for one source and replace prior chunks.

        Returns the number of chunks written. Idempotent on
        ``(source_kind, source_id)``: prior rows are deleted first.
        """
        ...

    async def search(
        self, query: str, *, k: int = 5
    ) -> list[Recalled]:
        """Return the top-``k`` chunks ranked by hybrid (cosine + BM25)."""
        ...

    async def delete(
        self, *, source_kind: SourceKind, source_id: str | None
    ) -> int:
        """Drop every chunk for one source. Returns the deleted count."""
        ...
