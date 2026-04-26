"""user_official_opt_outs repo: list/add/remove + idempotency."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.repositories import OptOutRepo

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_add_then_list(pg_session: AsyncSession) -> None:
    repo = OptOutRepo(pg_session)
    await repo.add("skill", "noisy-skill")
    await repo.add("instruction", "verbose-tone")
    rows = await repo.list()
    assert sorted(rows) == [
        ("instruction", "verbose-tone"),
        ("skill", "noisy-skill"),
    ]


async def test_add_is_idempotent(pg_session: AsyncSession) -> None:
    repo = OptOutRepo(pg_session)
    await repo.add("skill", "x")
    await repo.add("skill", "x")
    assert (await repo.list()) == [("skill", "x")]


async def test_remove(pg_session: AsyncSession) -> None:
    repo = OptOutRepo(pg_session)
    await repo.add("skill", "x")
    assert await repo.remove("skill", "x") is True
    assert await repo.remove("skill", "x") is False
    assert (await repo.list()) == []


async def test_list_filtered_by_entity(pg_session: AsyncSession) -> None:
    repo = OptOutRepo(pg_session)
    await repo.add("skill", "s1")
    await repo.add("skill", "s2")
    await repo.add("command", "c1")
    rows = await repo.list(entity="skill")
    assert sorted(s for _, s in rows) == ["s1", "s2"]


async def test_unknown_entity_rejected(pg_session: AsyncSession) -> None:
    repo = OptOutRepo(pg_session)
    with pytest.raises(ValueError):
        await repo.add("nope", "x")
    with pytest.raises(ValueError):
        await repo.remove("nope", "x")
    with pytest.raises(ValueError):
        await repo.list(entity="nope")
