"""Phase 41 RLS leak gate — cross-agent isolation.

Phase 41 extended the tenant_isolation policy on ``user_documents``,
``sessions``, and ``skill_proposals`` to require both ``app.user_id``
**and** ``app.agent_id`` to match. This test pretends to be the audit
team for the new axis: with one user, two agents (X and Y), inserting a
row scoped to agent X must be invisible to a connection scoped to
agent Y on the same user.

Same role discipline as ``test_rls_leak`` — uses ``litehorse_app`` so
the connection has neither ``SUPERUSER`` nor ``BYPASSRLS``. Skips when
Docker is unreachable.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_APP_ROLE = "litehorse_app"
_TENANT_TABLES = (
    "sessions",
    "messages",
    "user_documents",
    "skill_proposals",
    "agents",
)


async def _ensure_app_role(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
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
async def user_with_two_agents(
    _migrated_pg: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, str]:
    """Seed one user + two agents (X and Y); return (user_id, agent_x, agent_y)."""
    monkeypatch.setenv("LITEHORSE_DATABASE_URL", _migrated_pg)
    monkeypatch.setenv("LITEHORSE_ENV", "local")
    from lite_horse.config import get_settings
    from lite_horse.storage import db as db_mod

    get_settings.cache_clear()
    db_mod.reset_engine_for_tests()

    sessionmaker = db_mod.get_sessionmaker()
    await _ensure_app_role(sessionmaker)

    user_id = str(uuid4())
    agent_x = str(uuid4())
    agent_y = str(uuid4())
    async with sessionmaker() as bootstrap:
        async with bootstrap.begin():
            await bootstrap.execute(
                text(
                    "INSERT INTO users (id, external_id, role) "
                    "VALUES (:id, :ext, 'user')"
                ),
                {"id": user_id, "ext": f"agent-iso-{user_id[:8]}"},
            )
            for aid, slug, is_default in (
                (agent_x, "x", True),
                (agent_y, "y", False),
            ):
                await bootstrap.execute(
                    text(
                        "INSERT INTO agents (id, user_id, slug, name, "
                        "persona, permission_mode, enabled_tools, "
                        "is_default, created_at, updated_at) "
                        "VALUES (:id, :uid, :slug, :slug, '', 'auto', "
                        "'[]'::jsonb, :is_def, now(), now())"
                    ),
                    {
                        "id": aid,
                        "uid": user_id,
                        "slug": slug,
                        "is_def": is_default,
                    },
                )
    yield user_id, agent_x, agent_y
    async with sessionmaker() as cleanup:
        async with cleanup.begin():
            await cleanup.execute(
                text("DELETE FROM users WHERE id = :uid"),
                {"uid": user_id},
            )


async def _scope_to(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    agent_id: str | None,
) -> AsyncSession:
    session = sessionmaker()
    await session.begin()
    await session.execute(text(f"SET LOCAL ROLE {_APP_ROLE}"))
    await session.execute(
        text("SELECT set_config('app.user_id', :uid, true)"),
        {"uid": user_id},
    )
    await session.execute(
        text("SELECT set_config('app.agent_id', :aid, true)"),
        {"aid": agent_id or ""},
    )
    return session


async def test_user_doc_is_isolated_per_agent(
    user_with_two_agents: tuple[str, str, str],
) -> None:
    from lite_horse.storage import db as db_mod

    sessionmaker = db_mod.get_sessionmaker()
    user_id, agent_x, agent_y = user_with_two_agents

    sess_x = await _scope_to(sessionmaker, user_id=user_id, agent_id=agent_x)
    try:
        await sess_x.execute(
            text(
                "INSERT INTO user_documents (user_id, kind, agent_id, content) "
                "VALUES (:uid, 'memory.md', :aid, 'secret-X')"
            ),
            {"uid": user_id, "aid": agent_x},
        )
        await sess_x.commit()
    finally:
        await sess_x.close()

    # Agent Y on the same user must NOT see agent X's row.
    sess_y = await _scope_to(sessionmaker, user_id=user_id, agent_id=agent_y)
    try:
        result = await sess_y.execute(
            text(
                "SELECT content FROM user_documents WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        leaked = result.scalars().all()
        assert leaked == [], (
            f"agent-RLS leak — agent Y saw {leaked!r} from agent X"
        )
    finally:
        await sess_y.rollback()
        await sess_y.close()

    # The empty-string fallback (admin context) sees both — sanity check.
    sess_admin = await _scope_to(
        sessionmaker, user_id=user_id, agent_id=None
    )
    try:
        result = await sess_admin.execute(
            text(
                "SELECT content FROM user_documents WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        rows = result.scalars().all()
        assert "secret-X" in rows
    finally:
        await sess_admin.rollback()
        await sess_admin.close()


async def test_sessions_are_isolated_per_agent(
    user_with_two_agents: tuple[str, str, str],
) -> None:
    from lite_horse.storage import db as db_mod

    sessionmaker = db_mod.get_sessionmaker()
    user_id, agent_x, agent_y = user_with_two_agents

    sess_x = await _scope_to(sessionmaker, user_id=user_id, agent_id=agent_x)
    try:
        await sess_x.execute(
            text(
                "INSERT INTO sessions (id, user_id, agent_id, source, "
                "started_at) "
                "VALUES (:sid, :uid, :aid, 'web', now())"
            ),
            {"sid": "phase41-sess-x", "uid": user_id, "aid": agent_x},
        )
        await sess_x.commit()
    finally:
        await sess_x.close()

    sess_y = await _scope_to(sessionmaker, user_id=user_id, agent_id=agent_y)
    try:
        result = await sess_y.execute(
            text(
                "SELECT id FROM sessions WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        leaked = result.scalars().all()
        assert leaked == [], (
            f"agent-RLS leak on sessions — agent Y saw {leaked!r}"
        )
    finally:
        await sess_y.rollback()
        await sess_y.close()
