"""Migration round-trip against a real Postgres via testcontainers.

Skipped if Docker isn't reachable; full coverage runs under `make test-int`
or in CI where the daemon is up.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_url(monkeypatch_session: pytest.MonkeyPatch) -> Iterator[str]:
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # docker not reachable → skip
        pytest.skip(f"Docker unavailable: {exc!r}")

    raw_url = container.get_connection_url()
    # testcontainers gives a sync psycopg URL; rewrite for asyncpg.
    async_url = raw_url.replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")

    monkeypatch_session.setenv("LITEHORSE_DATABASE_URL", async_url)
    from lite_horse.config import get_settings

    get_settings.cache_clear()
    try:
        yield async_url
    finally:
        container.stop()


@pytest.fixture(scope="session")
def monkeypatch_session() -> Iterator[pytest.MonkeyPatch]:
    mpatch = pytest.MonkeyPatch()
    yield mpatch
    mpatch.undo()


def _alembic_command(args: list[str], db_url: str) -> None:
    import os
    import subprocess

    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["LITEHORSE_DATABASE_URL"] = db_url
    subprocess.run(
        [
            "uv",
            "run",
            "alembic",
            "-c",
            "src/lite_horse/alembic.ini",
            *args,
        ],
        check=True,
        cwd=repo_root,
        env=env,
    )


def _expected_tables() -> set[str]:
    return {
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
    }


def test_upgrade_then_downgrade(pg_url: str) -> None:
    import psycopg2

    _alembic_command(["upgrade", "head"], pg_url)

    sync_url = pg_url.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg2.connect(sync_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public'"
        )
        tables = {row[0] for row in cur.fetchall()}
        # alembic_version is created automatically; ignore.
        tables.discard("alembic_version")
        assert tables == _expected_tables()

        cur.execute(
            "SELECT relname FROM pg_class "
            "WHERE relrowsecurity AND relkind='r' AND relnamespace = "
            "(SELECT oid FROM pg_namespace WHERE nspname='public')"
        )
        rls_tables = {row[0] for row in cur.fetchall()}
        assert rls_tables == {
            "messages",
            "sessions",
            "user_documents",
            "skill_proposals",
        }

        cur.execute(
            "SELECT polname, polrelid::regclass::text "
            "FROM pg_policy ORDER BY polname, polrelid::regclass::text"
        )
        policies = cur.fetchall()
        policy_tables = {tbl for _, tbl in policies}
        assert policy_tables == {
            "messages",
            "sessions",
            "user_documents",
            "skill_proposals",
        }
        assert all(name == "tenant_isolation" for name, _ in policies)

    _alembic_command(["downgrade", "base"], pg_url)
    with psycopg2.connect(sync_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name <> 'alembic_version'"
        )
        assert cur.fetchall() == []
