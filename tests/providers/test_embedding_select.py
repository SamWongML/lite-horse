"""Phase 42 — embedding provider selection."""
from __future__ import annotations

import pytest

from lite_horse.providers.embedding import (
    NullEmbeddingProvider,
    select_embedding_provider,
)


def test_default_with_no_key_is_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITEHORSE_EMBEDDING_PROVIDER", raising=False)
    provider = select_embedding_provider(api_key=None)
    assert isinstance(provider, NullEmbeddingProvider)


def test_explicit_null_overrides_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITEHORSE_EMBEDDING_PROVIDER", "null")
    provider = select_embedding_provider(api_key="sk-anything")
    assert isinstance(provider, NullEmbeddingProvider)


def test_voyage_resolution_requires_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LITEHORSE_EMBEDDING_PROVIDER", "voyage")
    # No key → falls back to null.
    provider = select_embedding_provider(api_key=None)
    assert isinstance(provider, NullEmbeddingProvider)


def test_voyage_resolution_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITEHORSE_EMBEDDING_PROVIDER", "voyage")
    provider = select_embedding_provider(api_key="sk-voyage")
    # Voyage provider is its own class, but we don't import it here so we
    # just assert the name attribute.
    assert provider.name == "voyage"


def test_default_with_key_uses_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITEHORSE_EMBEDDING_PROVIDER", raising=False)
    provider = select_embedding_provider(api_key="sk-test")
    assert provider.name == "openai"
    assert provider.model == "text-embedding-3-small"


@pytest.mark.asyncio
async def test_null_embedder_returns_empty_list() -> None:
    provider = NullEmbeddingProvider()
    assert await provider.embed("anything") == []
    assert await provider.embed_batch(["a", "b"]) == [[], []]
