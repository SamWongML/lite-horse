"""OpenAI ``text-embedding-3-small`` provider — 1536-dim native.

Wraps ``openai.AsyncOpenAI``; the same SDK install used by the chat
completions provider. ``embed_batch`` issues one HTTP call per batch.

Cost is rolled into ``usage_events`` by the caller (recall_cloud); the
provider here just talks to the model.
"""
from __future__ import annotations

from openai import AsyncOpenAI

from lite_horse.constants import EMBED_DIM
from lite_horse.constants.models import MODEL_EMBEDDING_3_SMALL


class OpenAIEmbeddingProvider:
    name: str = "openai"
    model: str = MODEL_EMBEDDING_3_SMALL
    dim: int = EMBED_DIM

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            return []
        resp = await self._client.embeddings.create(
            model=self.model, input=text
        )
        return list(resp.data[0].embedding)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        non_empty_idx = [i for i, t in enumerate(texts) if t.strip()]
        if not non_empty_idx:
            return [[] for _ in texts]
        resp = await self._client.embeddings.create(
            model=self.model,
            input=[texts[i] for i in non_empty_idx],
        )
        out: list[list[float]] = [[] for _ in texts]
        for i, item in zip(non_empty_idx, resp.data, strict=False):
            out[i] = list(item.embedding)
        return out
