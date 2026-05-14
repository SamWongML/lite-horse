"""``EmbeddingProvider`` Protocol — text → 1536-dim float vector.

Two impls ship: :class:`OpenAIEmbeddingProvider`
(``text-embedding-3-small``, native 1536-dim) and
:class:`VoyageEmbeddingProvider` (``voyage-3``, 1024-dim padded to
1536). Selection is driven by the ``LITEHORSE_EMBEDDING_PROVIDER`` env
var; the registry returns the configured provider.

The Protocol is async because cloud impls go over HTTP. A no-op
``NullEmbeddingProvider`` is also available for the CLI ``no-embed``
fallback (the recall path then falls back to BM25-only).
"""
from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from lite_horse.constants import EMBED_DIM
from lite_horse.providers.embedding_openai import OpenAIEmbeddingProvider
from lite_horse.providers.embedding_voyage import VoyageEmbeddingProvider

__all__ = [
    "EMBED_DIM",
    "EmbeddingProvider",
    "NullEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "VoyageEmbeddingProvider",
    "select_embedding_provider",
]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embed text into a fixed-dim vector."""

    name: str
    model: str
    dim: int

    async def embed(self, text: str) -> list[float]:
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


class NullEmbeddingProvider:
    """Embeds nothing — returns ``[]`` so callers fall back to BM25.

    Used by the CLI ``no-embed`` mode (no API key configured) and by
    tests that don't care about cosine ranking.
    """

    name: str = "null"
    model: str = "none"
    dim: int = EMBED_DIM

    async def embed(self, text: str) -> list[float]:
        del text
        return []

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


def select_embedding_provider(
    api_key: str | None = None,
) -> EmbeddingProvider:
    """Return the configured :class:`EmbeddingProvider`.

    Resolution order:

    1. ``LITEHORSE_EMBEDDING_PROVIDER=null`` → :class:`NullEmbeddingProvider`.
    2. ``LITEHORSE_EMBEDDING_PROVIDER=voyage`` + ``api_key`` → Voyage.
    3. Otherwise → OpenAI ``text-embedding-3-small`` if ``api_key`` is
       set, else :class:`NullEmbeddingProvider`.
    """
    name = (os.environ.get("LITEHORSE_EMBEDDING_PROVIDER") or "").lower()
    if name == "null":
        return NullEmbeddingProvider()
    if name == "voyage" and api_key:
        return VoyageEmbeddingProvider(api_key=api_key)
    if api_key:
        return OpenAIEmbeddingProvider(api_key=api_key)
    return NullEmbeddingProvider()
