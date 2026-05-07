"""Cloud :class:`RecallBackend` impl — pgvector + BM25 hybrid.

Wraps :class:`MemoryChunkRepo`. Each method opens a short-lived
``db_session(user_id, agent_id)`` so the request connection isn't
pinned. Embedding writes are best-effort: if the provider call raises
or returns ``[]`` we still insert the row (with ``embedding=NULL``)
so BM25 retrieval works immediately; the embed worker patches the
vector in later.
"""
from __future__ import annotations

import logging

from lite_horse.agent.backends.recall import Recalled, SourceKind
from lite_horse.providers.chunker import chunk_text
from lite_horse.providers.embedding import EmbeddingProvider, NullEmbeddingProvider
from lite_horse.repositories.memory_chunk_repo import MemoryChunkRepo
from lite_horse.storage.db import db_session

log = logging.getLogger(__name__)


class RecallCloudBackend:
    """pgvector-backed recall for one (user, agent)."""

    def __init__(
        self,
        *,
        user_id: str,
        agent_id: str,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self.user_id = user_id
        self.agent_id = agent_id
        self._embedder = embedder or NullEmbeddingProvider()

    async def index(
        self,
        *,
        source_kind: SourceKind,
        source_id: str | None,
        content: str,
    ) -> int:
        chunks = chunk_text(content)
        async with db_session(self.user_id, self.agent_id) as session:
            repo = MemoryChunkRepo(session)
            await repo.delete_source(
                agent_id=self.agent_id,
                source_kind=source_kind,
                source_id=source_id,
            )
            if not chunks:
                return 0
            try:
                embeddings = await self._embedder.embed_batch(chunks)
            except Exception:
                log.warning(
                    "recall.index: embed_batch failed (kind=%s id=%s); "
                    "rows will be inserted with NULL embedding",
                    source_kind,
                    source_id,
                )
                embeddings = [[] for _ in chunks]
            for i, chunk in enumerate(chunks):
                emb = embeddings[i] if i < len(embeddings) else []
                await repo.insert(
                    agent_id=self.agent_id,
                    source_kind=source_kind,
                    source_id=source_id,
                    chunk_index=i,
                    content=chunk,
                    embedding=emb if emb else None,
                    embed_model=self._embedder.model,
                )
        return len(chunks)

    async def search(self, query: str, *, k: int = 5) -> list[Recalled]:
        if not query.strip():
            return []
        try:
            q_emb = await self._embedder.embed(query)
        except Exception:
            log.warning("recall.search: query embed failed; falling back to BM25")
            q_emb = []
        async with db_session(self.user_id, self.agent_id) as session:
            rows = await MemoryChunkRepo(session).hybrid_search(
                agent_id=self.agent_id,
                query=query,
                query_embedding=q_emb if q_emb else None,
                k=k,
            )
        return [
            Recalled(
                source_kind=r.source_kind,
                source_id=r.source_id,
                content=r.content,
                score=r.score,
                ts_iso=r.ts_iso,
            )
            for r in rows
        ]

    async def delete(
        self, *, source_kind: SourceKind, source_id: str | None
    ) -> int:
        async with db_session(self.user_id, self.agent_id) as session:
            return await MemoryChunkRepo(session).delete_source(
                agent_id=self.agent_id,
                source_kind=source_kind,
                source_id=source_id,
            )
