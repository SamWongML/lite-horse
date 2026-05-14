"""Fixed-window text chunking for memory recall.

One chunk is indexed per ``memory_chunks`` row. We chunk on whitespace
boundaries with a 256-token window and 32-token overlap; the
``tiktoken`` adapter is preferred when present so the window matches the
embedding model's tokeniser, and a character-based fallback covers
environments where ``tiktoken`` isn't installed.

The chunker is intentionally simple — no semantic boundary detection,
no hierarchical splits. Memory + skill bodies are short (≤ 2 400 chars
for memory.md; ≤ 15 KiB for SKILL.md) so the simple sliding window
gives enough recall fidelity without the complexity tax.
"""
from __future__ import annotations

try:
    import tiktoken
except ImportError:  # pragma: no cover - exercised on slim deploys
    tiktoken = None

CHUNK_TOKENS = 256
CHUNK_OVERLAP_TOKENS = 32
_CHARS_PER_TOKEN_FALLBACK = 4  # heuristic for the no-tiktoken path


def _chunk_with_tiktoken(text: str) -> list[str]:
    if tiktoken is None:
        raise RuntimeError("tiktoken not installed")
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if not tokens:
        return []
    out: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + CHUNK_TOKENS, len(tokens))
        out.append(enc.decode(tokens[start:end]).strip())
        if end == len(tokens):
            break
        start = max(end - CHUNK_OVERLAP_TOKENS, start + 1)
    return [c for c in out if c]


def _chunk_with_chars(text: str) -> list[str]:
    if not text.strip():
        return []
    window = CHUNK_TOKENS * _CHARS_PER_TOKEN_FALLBACK
    overlap = CHUNK_OVERLAP_TOKENS * _CHARS_PER_TOKEN_FALLBACK
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + window, len(text))
        out.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in out if c]


def chunk_text(text: str) -> list[str]:
    """Split ``text`` into ``CHUNK_TOKENS``-sized chunks with overlap.

    Falls back to a character-based estimate when ``tiktoken`` isn't
    importable.
    """
    if not text or not text.strip():
        return []
    try:
        return _chunk_with_tiktoken(text)
    except Exception:
        return _chunk_with_chars(text)
