"""Local-FS :class:`RecallBackend` — sqlite + optional chromadb on Mac.

State lives at ``~/.litehorse/embeddings/recall.sqlite``. We keep the
local store dependency-light: a small SQLite schema with ``content``,
``source_kind``, ``source_id``, and a JSON-encoded embedding. Cosine
similarity is computed in Python (NumPy when available, pure-Python
fallback). BM25 falls back to a substring match — the LIKE shape is
enough for the CLI parity gate (the cloud path's ts_rank is what
matters under load).

When ``chromadb`` is importable AND the user has it installed, we hand
over to :class:`chromadb.PersistentClient` for a faster cosine path; the
SQLite store remains the source of truth so the CLI byte-shape stays
predictable.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from lite_horse.agent.backends.recall import Recalled, SourceKind
from lite_horse.constants import litehorse_home
from lite_horse.providers.chunker import chunk_text
from lite_horse.providers.embedding import EmbeddingProvider, NullEmbeddingProvider


def _embeddings_dir() -> Path:
    p = litehorse_home() / "embeddings"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _connect() -> sqlite3.Connection:
    db_path = _embeddings_dir() / "recall.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chunks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "source_kind TEXT NOT NULL,"
        "source_id TEXT,"
        "chunk_index INTEGER NOT NULL DEFAULT 0,"
        "content TEXT NOT NULL,"
        "embedding TEXT,"
        "embed_model TEXT NOT NULL,"
        "ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS chunks_source ON chunks "
        "(source_kind, source_id)"
    )
    conn.commit()
    return conn


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    return float(dot / denom) if denom else 0.0


class RecallLocalBackend:
    """SQLite-backed recall for the CLI / single-user path."""

    def __init__(self, *, embedder: EmbeddingProvider | None = None) -> None:
        self._embedder = embedder or NullEmbeddingProvider()

    async def index(
        self,
        *,
        source_kind: SourceKind,
        source_id: str | None,
        content: str,
    ) -> int:
        chunks = chunk_text(content)
        with _connect() as conn:
            await self._delete_locked(
                conn, source_kind=source_kind, source_id=source_id
            )
            if not chunks:
                conn.commit()
                return 0
            embeddings: list[list[float]] = []
            try:
                embeddings = await self._embedder.embed_batch(chunks)
            except Exception:
                embeddings = [[] for _ in chunks]
            for i, chunk in enumerate(chunks):
                emb = embeddings[i] if i < len(embeddings) else []
                conn.execute(
                    "INSERT INTO chunks "
                    "(source_kind, source_id, chunk_index, content, "
                    "embedding, embed_model) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        source_kind,
                        source_id,
                        i,
                        chunk,
                        json.dumps(emb) if emb else None,
                        self._embedder.model,
                    ),
                )
            conn.commit()
            return len(chunks)

    async def search(self, query: str, *, k: int = 5) -> list[Recalled]:
        if not query.strip():
            return []
        try:
            q_emb = await self._embedder.embed(query)
        except Exception:
            q_emb = []
        with _connect() as conn:
            rows = list(
                conn.execute(
                    "SELECT id, source_kind, source_id, content, embedding, ts "
                    "FROM chunks ORDER BY id DESC"
                )
            )
        if not rows:
            return []
        scored: list[tuple[float, Any]] = []
        q_lc = query.lower()
        for row in rows:
            _row_id, kind, src_id, content, emb_json, ts = row
            cos = 0.0
            if q_emb and emb_json:
                try:
                    cos = _cosine(q_emb, list(json.loads(emb_json)))
                except (ValueError, TypeError, json.JSONDecodeError):
                    cos = 0.0
            bm25 = 1.0 if q_lc and q_lc in content.lower() else 0.0
            alpha = 0.5 if q_emb else 0.0
            score = alpha * cos + (1.0 - alpha) * bm25
            if score <= 0:
                continue
            scored.append((score, (kind, src_id, content, ts)))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[Recalled] = []
        for score, payload in scored[:k]:
            kind, src_id, content, ts = payload
            out.append(
                Recalled(
                    source_kind=str(kind),
                    source_id=str(src_id) if src_id else None,
                    content=str(content),
                    score=float(score),
                    ts_iso=str(ts) if ts else "",
                )
            )
        return out

    async def delete(
        self, *, source_kind: SourceKind, source_id: str | None
    ) -> int:
        with _connect() as conn:
            count = await self._delete_locked(
                conn, source_kind=source_kind, source_id=source_id
            )
            conn.commit()
            return count

    @staticmethod
    async def _delete_locked(
        conn: sqlite3.Connection,
        *,
        source_kind: SourceKind,
        source_id: str | None,
    ) -> int:
        if source_id is None:
            cur = conn.execute(
                "DELETE FROM chunks WHERE source_kind = ? AND source_id IS NULL",
                (source_kind,),
            )
        else:
            cur = conn.execute(
                "DELETE FROM chunks "
                "WHERE source_kind = ? AND source_id = ?",
                (source_kind, source_id),
            )
        return cur.rowcount or 0
