"""cron_jobs repo: user-scope CRUD + mark_fired bookkeeping."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.repositories import CronRepo

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_create_then_get_user(pg_session: AsyncSession) -> None:
    repo = CronRepo(pg_session)
    row = await repo.create_user(
        slug="standup",
        cron_expr="0 9 * * 1-5",
        prompt="post a standup summary",
        webhook_url="https://example.com/hook",
    )
    assert row.scope == "user"
    fetched = await repo.get_user("standup")
    assert fetched is not None
    assert fetched.cron_expr == "0 9 * * 1-5"
    assert fetched.webhook_url == "https://example.com/hook"


async def test_update_user_in_place(pg_session: AsyncSession) -> None:
    repo = CronRepo(pg_session)
    await repo.create_user(slug="x", cron_expr="* * * * *", prompt="p")
    updated = await repo.update_user(
        "x", cron_expr="0 * * * *", enabled=False
    )
    assert updated is not None
    assert updated.cron_expr == "0 * * * *"
    assert updated.enabled is False


async def test_update_user_clear_webhook(pg_session: AsyncSession) -> None:
    repo = CronRepo(pg_session)
    await repo.create_user(
        slug="x",
        cron_expr="* * * * *",
        prompt="p",
        webhook_url="https://hook",
    )
    updated = await repo.update_user("x", clear_webhook_url=True)
    assert updated is not None
    assert updated.webhook_url is None


async def test_mark_fired_resets_strikes(pg_session: AsyncSession) -> None:
    repo = CronRepo(pg_session)
    await repo.create_user(slug="x", cron_expr="* * * * *", prompt="p")
    when = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)
    await repo.mark_fired("x", when=when, reset_strikes=True)
    row = await repo.get_user("x")
    assert row is not None
    assert row.last_fired_at == when
    assert row.strikes == 0


async def test_delete_user(pg_session: AsyncSession) -> None:
    repo = CronRepo(pg_session)
    await repo.create_user(slug="x", cron_expr="* * * * *", prompt="p")
    assert await repo.delete_user("x") is True
    assert await repo.get_user("x") is None


async def test_list_user_orders_by_slug(pg_session: AsyncSession) -> None:
    repo = CronRepo(pg_session)
    for s in ("c", "a", "b"):
        await repo.create_user(slug=s, cron_expr="* * * * *", prompt="p")
    assert [r.slug for r in await repo.list_user()] == ["a", "b", "c"]
