"""Phase 39 RLS leak gate.

The Hard Contract guarantees tenant isolation by setting ``app.user_id``
on every Postgres connection and pairing it with RLS policies on every
tenant-scoped table. This test pretends to be the audit team: it
inserts a row owned by user A, then opens a fresh connection scoped to
user B and asserts the SELECT returns zero rows.

The testcontainers default role is a superuser, which bypasses RLS
even with ``FORCE ROW LEVEL SECURITY``. To match production (where the
app role has neither ``SUPERUSER`` nor ``BYPASSRLS``) the fixture
creates a dedicated ``litehorse_app`` role, grants it CRUD on the
tenant tables, and ``SET ROLE``-s to it before every query.

Skips cleanly if Docker / testcontainers are not available so unit
runs on a laptop without Docker still pass.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_APP_ROLE = "litehorse_app"
_TENANT_TABLES = ("sessions", "messages", "user_documents", "skill_proposals")


async def _ensure_app_role(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Create a non-superuser role with table CRUD; idempotent."""
    async with sessionmaker() as bootstrap:
        async with bootstrap.begin():
            await bootstrap.execute(
                text(
                    "DO $$ BEGIN "
                    f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{_APP_ROLE}') THEN "
                    f"CREATE ROLE {_APP_ROLE} NOSUPERUSER NOBYPASSRLS LOGIN; "
                    "END IF; END $$;"
                )
            )
            for table in (*_TENANT_TABLES, "users"):
                await bootstrap.execute(
                    text(
                        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} "
                        f"TO {_APP_ROLE}"
                    )
                )


@pytest_asyncio.fixture()
async def two_users(_migrated_pg: str, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Seed two distinct user rows; return their ids."""
    monkeypatch.setenv("LITEHORSE_DATABASE_URL", _migrated_pg)
    monkeypatch.setenv("LITEHORSE_ENV", "local")
    from lite_horse.config import get_settings
    from lite_horse.storage import db as db_mod

    get_settings.cache_clear()
    db_mod.reset_engine_for_tests()

    sessionmaker = db_mod.get_sessionmaker()
    await _ensure_app_role(sessionmaker)

    user_a = str(uuid4())
    user_b = str(uuid4())
    async with sessionmaker() as bootstrap:
        async with bootstrap.begin():
            for uid in (user_a, user_b):
                await bootstrap.execute(
                    text(
                        "INSERT INTO users (id, external_id, role) "
                        "VALUES (:id, :ext, 'user')"
                    ),
                    {"id": uid, "ext": f"leak-{uid[:8]}"},
                )
    yield user_a, user_b
    async with sessionmaker() as cleanup:
        async with cleanup.begin():
            await cleanup.execute(
                text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": [user_a, user_b]},
            )


async def _scope_to_user(
    sessionmaker: async_sessionmaker[AsyncSession], user_id: str
) -> AsyncSession:
    """Open a session, drop privilege to the app role, set the GUC."""
    session = sessionmaker()
    await session.begin()
    await session.execute(text(f"SET LOCAL ROLE {_APP_ROLE}"))
    await session.execute(
        text("SELECT set_config('app.user_id', :uid, true)"),
        {"uid": user_id},
    )
    return session


async def test_user_b_cannot_see_user_a_session_rows(
    two_users: tuple[str, str],
) -> None:
    """An RLS misconfiguration would leak rows across the GUC boundary."""
    from lite_horse.storage import db as db_mod

    sessionmaker = db_mod.get_sessionmaker()
    user_a, user_b = two_users

    # Insert a row as user A.
    sess_a = await _scope_to_user(sessionmaker, user_a)
    try:
        await sess_a.execute(
            text(
                "INSERT INTO sessions (id, user_id, source, started_at) "
                "VALUES (:sid, :uid, 'web', now())"
            ),
            {"sid": "sess-a-only", "uid": user_a},
        )
        await sess_a.commit()
    finally:
        await sess_a.close()

    # As user B, the same SELECT must return zero rows.
    sess_b = await _scope_to_user(sessionmaker, user_b)
    try:
        result = await sess_b.execute(
            text("SELECT id FROM sessions WHERE id = :sid"),
            {"sid": "sess-a-only"},
        )
        rows = result.scalars().all()
        assert rows == [], (
            f"RLS leak — user B saw {rows!r} for a session owned by user A"
        )
    finally:
        await sess_b.rollback()
        await sess_b.close()

    # Cleanup: as user A again, drop the row.
    sess_cleanup = await _scope_to_user(sessionmaker, user_a)
    try:
        await sess_cleanup.execute(
            text("DELETE FROM sessions WHERE id = :sid"),
            {"sid": "sess-a-only"},
        )
        await sess_cleanup.commit()
    finally:
        await sess_cleanup.close()


async def test_user_b_cannot_see_user_a_user_documents(
    two_users: tuple[str, str],
) -> None:
    """memory/user.md must respect RLS too."""
    from uuid import UUID

    from lite_horse.storage import db as db_mod

    sessionmaker = db_mod.get_sessionmaker()
    user_a, user_b = two_users

    sess_a = await _scope_to_user(sessionmaker, user_a)
    try:
        await sess_a.execute(
            text(
                "INSERT INTO user_documents (user_id, kind, content) "
                "VALUES (:uid, 'memory.md', 'secret-A')"
            ),
            {"uid": user_a},
        )
        await sess_a.commit()
    finally:
        await sess_a.close()

    sess_b = await _scope_to_user(sessionmaker, user_b)
    try:
        result = await sess_b.execute(
            text(
                "SELECT content FROM user_documents WHERE user_id = :uid"
            ),
            {"uid": user_a},
        )
        leaked = result.scalars().all()
        assert leaked == [], (
            f"RLS leak on user_documents — user B saw {leaked!r}"
        )
    finally:
        await sess_b.rollback()
        await sess_b.close()

    sess_cleanup = await _scope_to_user(sessionmaker, user_a)
    try:
        await sess_cleanup.execute(
            text("DELETE FROM user_documents WHERE user_id = :uid"),
            {"uid": UUID(user_a)},
        )
        await sess_cleanup.commit()
    finally:
        await sess_cleanup.close()
