"""instructions repo: user-scope CRUD + priority ordering on read."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.repositories import InstructionRepo

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_create_then_get(pg_session: AsyncSession) -> None:
    repo = InstructionRepo(pg_session)
    row = await repo.create_user(slug="tone", body="be terse", priority=50)
    assert row.priority == 50
    assert row.version == 1
    assert (await repo.get_user("tone")).body == "be terse"


async def test_list_user_orders_by_priority_then_slug(
    pg_session: AsyncSession,
) -> None:
    repo = InstructionRepo(pg_session)
    await repo.create_user(slug="b", body="b", priority=100)
    await repo.create_user(slug="a", body="a", priority=100)
    await repo.create_user(slug="z", body="z", priority=10)
    assert [r.slug for r in await repo.list_user()] == ["z", "a", "b"]


async def test_update_user_in_place(pg_session: AsyncSession) -> None:
    repo = InstructionRepo(pg_session)
    await repo.create_user(slug="x", body="old")
    row = await repo.update_user("x", body="new", priority=20)
    assert row is not None
    assert row.body == "new"
    assert row.priority == 20
    assert row.version == 1


async def test_delete_user(pg_session: AsyncSession) -> None:
    repo = InstructionRepo(pg_session)
    await repo.create_user(slug="x", body="b")
    assert await repo.delete_user("x") is True
    assert await repo.get_user("x") is None


async def test_list_official_only_returns_official(
    pg_session: AsyncSession,
) -> None:
    repo = InstructionRepo(pg_session)
    await repo.create_user(slug="user-only", body="u")
    await pg_session.execute(
        text(
            """
            INSERT INTO instructions
              (id, scope, user_id, slug, version, is_current, mandatory,
               priority, body)
            VALUES
              (gen_random_uuid(), 'official', NULL, 'safety', 1, true, true,
               10, 'safety baseline')
            """
        )
    )
    official = await repo.list_official()
    assert [r.slug for r in official] == ["safety"]
