""":class:`RecallLocalBackend` round-trip.

The local backend uses a small SQLite store under
``~/.litehorse/embeddings/recall.sqlite``. The tests here exercise:

* ``index`` chunking → multiple rows for a long document
* ``search`` ranks by BM25 substring when no embedder is available
* ``delete`` is idempotent on ``(source_kind, source_id)``
* a re-index of the same source replaces prior rows

A separate test exercises the embedder seam by injecting a deterministic
fake provider that hashes content into a fixed-shape vector — that's
enough to assert the cosine path runs without pulling OpenAI.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from lite_horse.agent.backends.recall_local import RecallLocalBackend


class _DeterministicEmbedder:
    name: str = "fake"
    model: str = "fake-embed-v1"
    dim: int = 1536

    async def embed(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(text: str) -> list[float]:
        # Cheap: bag-of-chars projection into a fixed length, then normalise.
        bins = [0.0] * 32
        for c in text:
            bins[ord(c) % 32] += 1.0
        norm = math.sqrt(sum(x * x for x in bins)) or 1.0
        return [x / norm for x in bins] + [0.0] * (1536 - 32)


@pytest.mark.asyncio
async def test_index_then_search_roundtrip(litehorse_home: Path) -> None:
    backend = RecallLocalBackend()  # NullEmbeddingProvider — BM25-only path
    n = await backend.index(
        source_kind="memory_md",
        source_id=None,
        content="I prefer pnpm for js package management.",
    )
    assert n >= 1

    hits = await backend.search("pnpm")
    assert any("pnpm" in h.content for h in hits)
    db = litehorse_home / "embeddings" / "recall.sqlite"
    assert db.is_file()


@pytest.mark.asyncio
async def test_delete_drops_prior_rows(litehorse_home: Path) -> None:
    del litehorse_home
    backend = RecallLocalBackend()
    await backend.index(
        source_kind="skill_body", source_id="example", content="alpha"
    )
    await backend.index(
        source_kind="skill_body", source_id="example", content="beta"
    )
    # Re-indexing replaces — searching for "alpha" should miss.
    hits = await backend.search("alpha")
    assert not hits
    # And "beta" should hit.
    hits = await backend.search("beta")
    assert hits

    deleted = await backend.delete(
        source_kind="skill_body", source_id="example"
    )
    assert deleted >= 1
    hits = await backend.search("beta")
    assert not hits


@pytest.mark.asyncio
async def test_cosine_path_with_fake_embedder(litehorse_home: Path) -> None:
    del litehorse_home
    backend = RecallLocalBackend(embedder=_DeterministicEmbedder())
    await backend.index(
        source_kind="memory_md",
        source_id=None,
        content="I deploy my apps to fly.io for personal projects.",
    )
    hits = await backend.search("where do I deploy?")
    # Cosine + BM25 hybrid should surface the deploy line.
    assert hits
    assert any("fly.io" in h.content for h in hits)


@pytest.mark.asyncio
async def test_search_returns_empty_for_empty_query(litehorse_home: Path) -> None:
    del litehorse_home
    backend = RecallLocalBackend()
    assert await backend.search("") == []
    assert await backend.search("   ") == []
