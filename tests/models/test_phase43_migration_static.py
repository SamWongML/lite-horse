"""Pure-Python sanity checks on the Phase 43 session_summaries migration.

The live round-trip lives in :mod:`tests.models.test_migration_roundtrip`.
This file just inspects the migration text so a sloppy edit can't drop
a column, the index, or an RLS step silently.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path


def _migration_source() -> str:
    versions = resources.files("lite_horse.alembic.versions")
    paths = [
        p
        for p in versions.iterdir()
        if str(p).endswith("0005_phase43_summaries.py")
    ]
    assert paths, "phase 43 migration not found"
    return Path(str(paths[0])).read_text(encoding="utf-8")


def test_creates_session_summaries_table() -> None:
    src = _migration_source()
    assert "create_table" in src and '"session_summaries"' in src
    for column in ("session_id", "user_id", "agent_id", "summary", "topic", "generator", "generated_at"):
        assert column in src, f"missing column {column!r} in migration"


def test_chains_off_phase42() -> None:
    src = _migration_source()
    assert 'revision: str = "0005_phase43_summaries"' in src
    assert 'down_revision: str | None = "0004_phase42_pgvector"' in src


def test_creates_tenant_index() -> None:
    src = _migration_source()
    assert "session_summaries_tenant" in src
    assert "generated_at DESC" in src


def test_enables_rls_with_compound_policy() -> None:
    src = _migration_source()
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "FORCE ROW LEVEL SECURITY" in src
    assert "current_setting('app.user_id', true)" in src
    assert "current_setting('app.agent_id', true)" in src
    assert "tenant_isolation" in src


def test_downgrade_reverses_changes() -> None:
    src = _migration_source()
    assert "DROP POLICY IF EXISTS tenant_isolation" in src
    assert "drop_table" in src and '"session_summaries"' in src
