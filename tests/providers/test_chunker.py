"""Phase 42 — fixed-window chunker behaviour."""
from __future__ import annotations

from lite_horse.providers.chunker import (
    CHUNK_OVERLAP_TOKENS,
    CHUNK_TOKENS,
    chunk_text,
)


def test_empty_text_returns_empty_list() -> None:
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_short_text_is_single_chunk() -> None:
    chunks = chunk_text("I prefer pnpm")
    assert chunks == ["I prefer pnpm"]


def test_long_text_splits_into_multiple_chunks() -> None:
    body = "This is a sentence. " * 200  # well past the window
    chunks = chunk_text(body)
    assert len(chunks) > 1
    # Every chunk is non-empty.
    assert all(c.strip() for c in chunks)
    # First chunk and second chunk overlap on at least one substring.
    assert any(seg and seg in chunks[1] for seg in chunks[0].split() if seg)


def test_window_constants_are_reasonable() -> None:
    assert CHUNK_TOKENS > CHUNK_OVERLAP_TOKENS
    assert CHUNK_OVERLAP_TOKENS > 0
