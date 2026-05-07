"""Pure-Python sanity checks on the Phase 42 pgvector migration.

Live round-trip lives in :mod:`tests.models.test_migration_roundtrip`.
This file just inspects the migration text so a sloppy edit can't drop
a column or RLS step silently.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path


def _migration_source() -> str:
    versions = resources.files("lite_horse.alembic.versions")
    paths = [
        p
        for p in versions.iterdir()
        if str(p).endswith("0004_phase42_pgvector.py")
    ]
    assert paths, "phase 42 migration not found"
    return Path(str(paths[0])).read_text(encoding="utf-8")


def test_creates_vector_extension() -> None:
    src = _migration_source()
    assert "CREATE EXTENSION IF NOT EXISTS vector" in src


def test_creates_memory_chunks_table() -> None:
    src = _migration_source()
    assert "create_table" in src and '"memory_chunks"' in src
    # Source kind constraint enumerates every supported source.
    for kind in (
        "memory_md",
        "user_md",
        "session_summary",
        "message",
        "skill_body",
    ):
        assert kind in src


def test_adds_tsv_and_embedding_columns() -> None:
    src = _migration_source()
    assert "to_tsvector('simple', content)" in src
    # 1536-dim vector column is the only supported shape this phase.
    assert "vector(1536)" in src


def test_creates_vector_and_tsv_indexes() -> None:
    src = _migration_source()
    assert "memory_chunks_tenant" in src
    # GIN index for BM25.
    assert "memory_chunks_tsv" in src
    assert "USING GIN" in src
    # HNSW index for cosine.
    assert "memory_chunks_vec" in src
    assert "USING hnsw" in src
    assert "vector_cosine_ops" in src


def test_enables_rls_with_compound_policy() -> None:
    src = _migration_source()
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "FORCE ROW LEVEL SECURITY" in src
    # Compound (user + agent) policy — empty-string fallback for admin.
    assert "current_setting('app.user_id', true)" in src
    assert "current_setting('app.agent_id', true)" in src
