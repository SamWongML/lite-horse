"""Pure-Python sanity checks on the initial Alembic migration.

These run without Docker — the round-trip test against a real Postgres
lives in `test_migration_roundtrip.py` and is gated on
`@pytest.mark.integration`.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

EXPECTED_TABLES = (
    "users",
    "skills",
    "instructions",
    "commands",
    "mcp_servers",
    "cron_jobs",
    "user_official_opt_outs",
    "user_documents",
    "sessions",
    "messages",
    "skill_proposals",
    "audit_log",
    "usage_events",
)

RLS_TABLES = ("messages", "sessions", "user_documents", "skill_proposals")


def _migration_source() -> str:
    versions = resources.files("lite_horse.alembic.versions")
    paths = [p for p in versions.iterdir() if str(p).endswith("0001_initial_schema.py")]
    assert paths, "initial migration not found"
    return Path(str(paths[0])).read_text(encoding="utf-8")


def test_every_table_is_created_in_upgrade() -> None:
    src = _migration_source()
    for table in EXPECTED_TABLES:
        assert f'"{table}"' in src or f"'{table}'" in src, f"missing table: {table}"


def test_rls_policies_present() -> None:
    src = _migration_source()
    assert "ENABLE ROW LEVEL SECURITY" in src
    for table in RLS_TABLES:
        assert table in src
    assert "tenant_isolation" in src
    assert "current_setting('app.user_id', true)" in src


def test_advisory_lock_guard_in_env() -> None:
    env_py = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "lite_horse"
        / "alembic"
        / "env.py"
    ).read_text(encoding="utf-8")
    assert "pg_advisory_lock" in env_py
    assert "pg_advisory_unlock" in env_py


def test_partial_unique_indexes_present() -> None:
    src = _migration_source()
    for entity in ("skills", "instructions", "commands"):
        assert f"{entity}_official_slug_current" in src
        assert f"{entity}_user_slug_current" in src
    assert "mcp_official_slug_current" in src
    assert "mcp_user_slug_current" in src
    assert "scope='official' AND is_current" in src


def test_generated_tsvector_present() -> None:
    src = _migration_source()
    assert "to_tsvector('simple'" in src
    assert "Computed" in src
    assert "messages_tsv" in src
    assert "postgresql_using=\"gin\"" in src
