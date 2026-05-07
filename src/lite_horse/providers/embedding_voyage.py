"""Voyage AI ``voyage-3`` embedding provider.

Voyage returns 1024-dim vectors natively; we right-pad with zeros to
1536 dims so ``memory_chunks.embedding`` stays a single fixed-shape
column (the schema is sized once at migration time and we don't want
two columns side-by-side in a single index). Padding only changes the
magnitude in the unused tail and leaves cosine ranking unchanged
(Voyage's similarity is already L2-normalised).

Hits the public ``api.voyageai.com/v1/embeddings`` endpoint via httpx.
The ``voyageai`` SDK is optional — keeping the dep tree slim by using
the HTTP shape directly.
"""
from __future__ import annotations

import httpx

from lite_horse.constants import EMBED_DIM

_VOYAGE_DIM = 1024
_VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"


def _pad_to_target(vec: list[float]) -> list[float]:
    if len(vec) == EMBED_DIM:
        return vec
    if len(vec) > EMBED_DIM:
        return vec[:EMBED_DIM]
    return vec + [0.0] * (EMBED_DIM - len(vec))


class VoyageEmbeddingProvider:
    name: str = "voyage"
    model: str = "voyage-3"
    dim: int = EMBED_DIM

    def __init__(self, *, api_key: str, base_url: str = _VOYAGE_URL) -> None:
        self._api_key = api_key
        self._url = base_url

    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            return []
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"input": [text], "model": self.model},
            )
            resp.raise_for_status()
            payload = resp.json()
        emb = list(payload["data"][0]["embedding"])
        return _pad_to_target(emb)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        non_empty_idx = [i for i, t in enumerate(texts) if t.strip()]
        if not non_empty_idx:
            return [[] for _ in texts]
        body = {
            "input": [texts[i] for i in non_empty_idx],
            "model": self.model,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
            )
            resp.raise_for_status()
            payload = resp.json()
        out: list[list[float]] = [[] for _ in texts]
        for i, item in zip(non_empty_idx, payload["data"], strict=False):
            out[i] = _pad_to_target(list(item["embedding"]))
        return out
