"""Per-package test fixtures: spin up Postgres + apply migrations + tenant GUC.

All tests under ``tests/repositories`` use these — gives every test a live
``AsyncSession`` already pinned to a freshly-created user, so writing a repo
test reads like writing the production caller.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text


@pytest.fixture(scope="session")
def _pg_url() -> Iterator[str]:
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
    async_url = raw_url.replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")
    try:
        yield async_url
    finally:
        container.stop()


@pytest.fixture(scope="session")
def _migrated_pg(_pg_url: str) -> str:
    """Apply alembic migrations once per session against the running PG."""
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["LITEHORSE_DATABASE_URL"] = _pg_url
    subprocess.run(
        [
            "uv",
            "run",
            "alembic",
            "-c",
            "src/lite_horse/alembic.ini",
            "upgrade",
            "head",
        ],
        check=True,
        cwd=repo_root,
        env=env,
    )
    return _pg_url


@pytest_asyncio.fixture()
async def pg_session(
    _migrated_pg: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Any]:
    """Yield a tenant-scoped ``AsyncSession`` for the current test.

    Creates a fresh user per test, sets ``app.user_id`` to that uuid, and
    rolls back at the end so tests don't see each other's rows.
    """
    monkeypatch.setenv("LITEHORSE_DATABASE_URL", _migrated_pg)
    monkeypatch.setenv("LITEHORSE_ENV", "local")
    from lite_horse.config import get_settings
    from lite_horse.storage import db as db_mod

    get_settings.cache_clear()
    db_mod.reset_engine_for_tests()

    sessionmaker = db_mod.get_sessionmaker()
    user_id = str(uuid4())
    external_id = f"repo-test-{user_id[:8]}"

    # Seed a user without RLS interfering: open a tx with no GUC, insert
    # the user (RLS only applies to the four tenant-scoped tables, not
    # ``users``).
    async with sessionmaker() as bootstrap:
        async with bootstrap.begin():
            await bootstrap.execute(
                text(
                    "INSERT INTO users (id, external_id, role) "
                    "VALUES (:id, :ext, 'user')"
                ),
                {"id": user_id, "ext": external_id},
            )

    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.user_id', :uid, true)"),
                {"uid": user_id},
            )
            yield session
            await session.rollback()

    # Cleanup user (cascades clean up sessions/messages).
    async with sessionmaker() as cleanup:
        async with cleanup.begin():
            await cleanup.execute(
                text("DELETE FROM users WHERE id = :id"), {"id": user_id}
            )

    await db_mod.dispose_engine()


pytestmark = pytest.mark.integration
