"""Pure-Python sanity checks on the Phase 45 promotion migration.

The live round-trip lives in :mod:`tests.models.test_migration_roundtrip`.
This file just inspects the migration text so a sloppy edit can't drop
a column, the partial unique index, or the chained ``down_revision``
silently.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path


def _migration_source() -> str:
    versions = resources.files("lite_horse.alembic.versions")
    paths = [
        p
        for p in versions.iterdir()
        if str(p).endswith("0007_phase45_promotion.py")
    ]
    assert paths, "phase 45 migration not found"
    return Path(str(paths[0])).read_text(encoding="utf-8")


def test_creates_skill_promotion_candidates_table() -> None:
    src = _migration_source()
    assert "create_table" in src and '"skill_promotion_candidates"' in src
    for col in (
        "source_skill_id",
        "frontmatter_name",
        "unique_user_count",
        "use_count",
        "success_rate",
        "status",
        "reason",
        "decided_by",
        "promoted_skill_id",
        "generated_at",
        "decided_at",
    ):
        assert col in src, f"missing column {col!r}"


def test_chains_off_phase44() -> None:
    src = _migration_source()
    assert 'revision: str = "0007_phase45_promotion"' in src
    assert 'down_revision: str | None = "0006_phase44_curator"' in src


def test_status_check_constraint() -> None:
    src = _migration_source()
    assert "'pending'" in src and "'promoted'" in src and "'rejected'" in src
    assert "skill_promotion_candidates_status_check" in src


def test_partial_unique_pending_index() -> None:
    src = _migration_source()
    assert "skill_promotion_candidates_pending_name" in src
    assert "WHERE status = 'pending'" in src


def test_downgrade_reverses_changes() -> None:
    src = _migration_source()
    assert (
        "DROP INDEX IF EXISTS skill_promotion_candidates_pending_name" in src
    )
    assert 'drop_table("skill_promotion_candidates")' in src
