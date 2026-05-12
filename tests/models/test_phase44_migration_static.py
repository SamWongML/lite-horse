"""Pure-Python sanity checks on the Phase 44 curator migration.

The live round-trip lives in :mod:`tests.models.test_migration_roundtrip`.
This file just inspects the migration text so a sloppy edit can't drop
a column, the RLS step, or the chained ``down_revision`` silently.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path


def _migration_source() -> str:
    versions = resources.files("lite_horse.alembic.versions")
    paths = [
        p
        for p in versions.iterdir()
        if str(p).endswith("0006_phase44_curator.py")
    ]
    assert paths, "phase 44 migration not found"
    return Path(str(paths[0])).read_text(encoding="utf-8")


def test_creates_turn_outcomes_table() -> None:
    src = _migration_source()
    assert "create_table" in src and '"turn_outcomes"' in src
    for col in (
        "user_id",
        "agent_id",
        "session_id",
        "turn_id",
        "source",
        "rating",
        "reason",
        "skill_slug",
        "ts",
    ):
        assert col in src, f"missing column {col!r}"


def test_chains_off_phase43() -> None:
    src = _migration_source()
    assert 'revision: str = "0006_phase44_curator"' in src
    assert 'down_revision: str | None = "0005_phase43_summaries"' in src


def test_source_and_rating_check_constraints() -> None:
    src = _migration_source()
    assert "'classifier'" in src and "'user_explicit'" in src and "'regex_marker'" in src
    assert "rating BETWEEN -1 AND 1" in src


def test_rls_compound_policy_present() -> None:
    src = _migration_source()
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "FORCE ROW LEVEL SECURITY" in src
    assert "current_setting('app.user_id', true)" in src
    assert "current_setting('app.agent_id', true)" in src
    assert "tenant_isolation" in src


def test_adds_curator_columns_to_skills() -> None:
    src = _migration_source()
    for col in (
        "use_count",
        "success_count",
        "error_count",
        "last_used_at",
        "curator_state",
    ):
        assert col in src, f"missing skills column {col!r}"
    assert "skills_curator_state_check" in src
    assert "'active'" in src and "'stale'" in src
    assert "'archived'" in src and "'pinned'" in src


def test_downgrade_reverses_changes() -> None:
    src = _migration_source()
    assert "DROP POLICY IF EXISTS tenant_isolation" in src
    assert 'drop_table' in src and '"turn_outcomes"' in src
    for col in (
        "curator_state",
        "last_used_at",
        "error_count",
        "success_count",
        "use_count",
    ):
        assert f'drop_column("skills", "{col}")' in src
