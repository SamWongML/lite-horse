"""Embed backfill worker — fills NULL embeddings on memory_chunks rows.

Phase 42 inserts ``memory_chunks`` rows synchronously on every memory /
skill write. Embedding the chunk text takes one HTTP round-trip to the
embedding provider, so when the provider is slow / down the row is
inserted with ``embedding=NULL`` and BM25 alone serves recall until
this worker re-fills it.

The worker message body is

    {"kind": "embed", "user_id": "...", "agent_id": "...", "limit": 50}

The handler grabs up to ``limit`` chunks with NULL embedding, embeds
them in one batch, and writes the vectors back. Idempotent: if no
NULL rows exist the handler is a no-op.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from lite_horse.providers.embedding import (
    EmbeddingProvider,
    select_embedding_provider,
)
from lite_horse.repositories.memory_chunk_repo import MemoryChunkRepo
from lite_horse.storage.db import db_session

log = logging.getLogger(__name__)

EMBED_KIND = "embed"
DEFAULT_BATCH_LIMIT = 50


@dataclass(frozen=True)
class EmbedMessage:
    kind: str
    user_id: str
    agent_id: str
    limit: int = DEFAULT_BATCH_LIMIT

    @classmethod
    def new(
        cls,
        *,
        user_id: str,
        agent_id: str,
        limit: int = DEFAULT_BATCH_LIMIT,
    ) -> EmbedMessage:
        return cls(
            kind=EMBED_KIND,
            user_id=user_id,
            agent_id=agent_id,
            limit=limit,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> EmbedMessage:
        data = json.loads(raw)
        return cls(
            kind=str(data["kind"]),
            user_id=str(data["user_id"]),
            agent_id=str(data["agent_id"]),
            limit=int(data.get("limit", DEFAULT_BATCH_LIMIT)),
        )


def is_embed_payload(raw: str) -> bool:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("kind") == EMBED_KIND


async def run_embed(
    message: EmbedMessage,
    *,
    embedder: EmbeddingProvider | None = None,
) -> bool:
    """Embed up to ``message.limit`` NULL-embedding rows for one tenant."""
    chosen = embedder or select_embedding_provider()
    async with db_session(message.user_id, message.agent_id) as session:
        repo = MemoryChunkRepo(session)
        pending = await repo.list_pending_embeddings(
            agent_id=message.agent_id, limit=message.limit
        )
        if not pending:
            return True
        ids = [row[0] for row in pending]
        texts = [row[1] for row in pending]
        try:
            embeddings = await chosen.embed_batch(texts)
        except Exception:
            log.exception(
                "embed: provider failed (user=%s agent=%s n=%d)",
                message.user_id,
                message.agent_id,
                len(texts),
            )
            return False
        filled = 0
        for chunk_id, emb in zip(ids, embeddings, strict=False):
            if not emb:
                continue
            await repo.set_embedding(
                chunk_id=chunk_id,
                embedding=list(emb),
                embed_model=chosen.model,
            )
            filled += 1
    log.info(
        "embed: filled %d/%d rows (user=%s agent=%s)",
        filled,
        len(texts),
        message.user_id,
        message.agent_id,
    )
    return True
