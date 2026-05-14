"""Pure-Python sanity checks on the agents migration.

Runs without Docker — the live round-trip is in
``test_migration_roundtrip.py``. These checks make sure the migration
text contains the expected DDL fragments so a sloppy edit can't drop
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
        if str(p).endswith("0003_agents.py")
    ]
    assert paths, "agents migration not found"
    return Path(str(paths[0])).read_text(encoding="utf-8")


def test_creates_agents_table() -> None:
    src = _migration_source()
    assert 'create_table' in src and '"agents"' in src
    assert "agents_user_slug_unique" in src
    assert "agents_one_default_per_user" in src


def test_adds_agent_id_columns() -> None:
    src = _migration_source()
    for table in (
        "user_documents",
        "skills",
        "cron_jobs",
        "sessions",
        "skill_proposals",
        "mcp_servers",
        "commands",
        "instructions",
    ):
        assert table in src


def test_adds_default_agent_id_to_users() -> None:
    src = _migration_source()
    assert '"default_agent_id"' in src
    assert "fk_users_default_agent" in src


def test_extends_rls_to_compound_policy() -> None:
    src = _migration_source()
    assert "current_setting('app.agent_id', true)" in src
    assert "tenant_isolation" in src
    # Compound clause shape — both axes must be referenced together.
    assert "user_id::text = current_setting('app.user_id', true)" in src
    assert "agent_id::text = current_setting('app.agent_id', true)" in src


def test_backfill_creates_one_default_per_user() -> None:
    src = _migration_source()
    # The DML inserts one default agent per user (idempotent guard).
    assert "INSERT INTO agents" in src
    assert "FROM users u" in src
    assert "is_default" in src
