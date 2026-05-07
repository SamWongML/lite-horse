"""``memory_chunks`` repository — pgvector hybrid retrieval.

Owns the **only** SQL paths into ``memory_chunks``: insert, delete, and
``hybrid_search`` (BM25 + cosine blend). The agent-side recall backend
(:mod:`lite_horse.agent.backends.recall_cloud`) is the only direct
caller — the rest of the codebase reaches recall through the backend
Protocol.

The hybrid score is ``alpha * cosine_sim(q, c) + (1 - alpha) * bm25(q, c)``,
normalised so each component sits in [0, 1] before blending. Rows
without an embedding (``embedding IS NULL`` — the worker hasn't filled
it in yet) still come back via the BM25 leg; cosine contributes 0 for
those rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import bindparam, delete, text

from lite_horse.models.memory_chunk import MemoryChunk
from lite_horse.repositories.base import BaseRepo


@dataclass(frozen=True)
class ChunkRow:
    """Hybrid-search hit. ``score`` is the blended BM25 + cosine score."""

    id: int
    source_kind: str
    source_id: str | None
    content: str
    score: float
    ts_iso: str


def _vector_literal(embedding: list[float]) -> str:
    """Render a Python list of floats as the pgvector text literal."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


class MemoryChunkRepo(BaseRepo):
    """memory_chunks CRUD + hybrid search."""

    async def insert(
        self,
        *,
        agent_id: str,
        source_kind: str,
        source_id: str | None,
        chunk_index: int,
        content: str,
        embedding: list[float] | None,
        embed_model: str,
    ) -> int:
        """Append one chunk row. Returns the assigned ``id``.

        ``embedding=None`` is allowed — the embed worker fills it in
        later via :meth:`set_embedding`. The ``tsv`` column is filled
        automatically by the GENERATED ALWAYS expression.
        """
        user_id = UUID(await self.current_user_id())
        params: dict[str, object] = {
            "user_id": user_id,
            "agent_id": UUID(agent_id),
            "source_kind": source_kind,
            "source_id": source_id,
            "chunk_index": chunk_index,
            "content": content,
            "embed_model": embed_model,
        }
        embedding_sql = "NULL"
        if embedding is not None:
            params["embedding_lit"] = _vector_literal(embedding)
            embedding_sql = "CAST(:embedding_lit AS vector)"
        stmt = text(
            "INSERT INTO memory_chunks "
            "(user_id, agent_id, source_kind, source_id, chunk_index, "
            "content, embedding, embed_model) VALUES "
            "(:user_id, :agent_id, :source_kind, :source_id, :chunk_index, "
            f":content, {embedding_sql}, :embed_model) "
            "RETURNING id"
        )
        row_id = (await self.session.execute(stmt, params)).scalar_one()
        return int(row_id)

    async def set_embedding(
        self, *, chunk_id: int, embedding: list[float], embed_model: str
    ) -> None:
        """Patch one row's ``embedding`` + ``embed_model`` (worker path)."""
        await self.session.execute(
            text(
                "UPDATE memory_chunks SET "
                "embedding = CAST(:embedding_lit AS vector), "
                "embed_model = :embed_model "
                "WHERE id = :chunk_id"
            ),
            {
                "chunk_id": chunk_id,
                "embedding_lit": _vector_literal(embedding),
                "embed_model": embed_model,
            },
        )

    async def delete_source(
        self, *, agent_id: str, source_kind: str, source_id: str | None
    ) -> int:
        """Remove every chunk for one source (idempotent re-index)."""
        user_id = UUID(await self.current_user_id())
        stmt = delete(MemoryChunk).where(
            MemoryChunk.user_id == user_id,
            MemoryChunk.agent_id == UUID(agent_id),
            MemoryChunk.source_kind == source_kind,
        )
        if source_id is None:
            stmt = stmt.where(MemoryChunk.source_id.is_(None))
        else:
            stmt = stmt.where(MemoryChunk.source_id == source_id)
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)

    async def list_pending_embeddings(
        self, *, agent_id: str, limit: int = 50
    ) -> list[tuple[int, str]]:
        """Return ``(id, content)`` for chunks with NULL embedding."""
        user_id = UUID(await self.current_user_id())
        stmt = text(
            "SELECT id, content FROM memory_chunks "
            "WHERE user_id = :user_id AND agent_id = :agent_id "
            "AND embedding IS NULL "
            "ORDER BY id ASC LIMIT :limit"
        )
        rows = (
            await self.session.execute(
                stmt,
                {
                    "user_id": user_id,
                    "agent_id": UUID(agent_id),
                    "limit": limit,
                },
            )
        ).all()
        return [(int(r.id), str(r.content)) for r in rows]

    async def hybrid_search(
        self,
        *,
        agent_id: str | None,
        query: str,
        query_embedding: list[float] | None = None,
        k: int = 5,
        alpha: float = 0.5,
    ) -> list[ChunkRow]:
        """Return top-``k`` chunks blended by cosine + BM25.

        ``alpha`` is the cosine weight; ``1 - alpha`` is the BM25 weight.
        When ``query_embedding`` is None (no-embed CLI path), the search
        falls back to pure BM25. ``agent_id`` narrows results to one
        agent; pass ``None`` to span every agent for the user.
        """
        if not query.strip():
            return []
        user_id = UUID(await self.current_user_id())

        params: dict[str, object] = {
            "user_id": user_id,
            "query": query,
            "k": int(k),
            "alpha": float(alpha),
        }
        agent_filter = ""
        if agent_id is not None:
            params["agent_id"] = UUID(agent_id)
            agent_filter = "AND agent_id = :agent_id"

        if query_embedding is not None:
            params["q_emb"] = _vector_literal(query_embedding)
            sql = (
                "WITH scored AS (\n"
                "  SELECT id, source_kind, source_id, content, ts,\n"
                "    CASE WHEN embedding IS NULL THEN 0.0\n"
                "         ELSE 1.0 - (embedding <=> CAST(:q_emb AS vector))\n"
                "    END AS cos_sim,\n"
                "    ts_rank_cd(tsv, plainto_tsquery('simple', :query)) AS bm25\n"
                "  FROM memory_chunks\n"
                f"  WHERE user_id = :user_id {agent_filter}\n"
                ")\n"
                "SELECT id, source_kind, source_id, content, ts,\n"
                "  (:alpha * cos_sim + (1 - :alpha) "
                "   * LEAST(bm25 / NULLIF(\n"
                "       (SELECT GREATEST(MAX(bm25), 1e-9) FROM scored), 0\n"
                "   ), 1.0)) AS score\n"
                "FROM scored\n"
                "WHERE cos_sim > 0 OR bm25 > 0\n"
                "ORDER BY score DESC\n"
                "LIMIT :k"
            )
        else:
            sql = (
                "WITH scored AS (\n"
                "  SELECT id, source_kind, source_id, content, ts,\n"
                "    ts_rank_cd(tsv, plainto_tsquery('simple', :query)) AS bm25\n"
                "  FROM memory_chunks\n"
                f"  WHERE user_id = :user_id {agent_filter}\n"
                "    AND tsv @@ plainto_tsquery('simple', :query)\n"
                ")\n"
                "SELECT id, source_kind, source_id, content, ts,\n"
                "  bm25 AS score\n"
                "FROM scored\n"
                "ORDER BY score DESC\n"
                "LIMIT :k"
            )
        stmt = text(sql).bindparams(bindparam("k", type_=None))
        rows = (await self.session.execute(stmt, params)).all()
        out: list[ChunkRow] = []
        for r in rows:
            out.append(
                ChunkRow(
                    id=int(r.id),
                    source_kind=str(r.source_kind),
                    source_id=str(r.source_id) if r.source_id else None,
                    content=str(r.content),
                    score=float(r.score or 0.0),
                    ts_iso=r.ts.isoformat() if r.ts else "",
                )
            )
        return out
