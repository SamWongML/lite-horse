"""Tenant-scoped Postgres session repository tests."""
from __future__ import annotations

import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.repositories import SessionRepo

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_create_then_get_meta_returns_dict(pg_session: AsyncSession) -> None:
    repo = SessionRepo(pg_session)
    await repo.create_session(session_id="s1", source="cli", model="gpt-test")
    meta = await repo.get_session_meta("s1")
    assert meta is not None
    assert meta["id"] == "s1"
    assert meta["source"] == "cli"
    assert meta["model"] == "gpt-test"
    assert isinstance(meta["started_at"], float)
    assert meta["ended_at"] is None


async def test_create_session_is_idempotent(pg_session: AsyncSession) -> None:
    repo = SessionRepo(pg_session)
    await repo.create_session(session_id="s1", source="cli")
    await repo.create_session(session_id="s1", source="cli")  # ON CONFLICT no-op
    metas = await repo.list_recent_sessions(limit=10)
    assert [m["id"] for m in metas] == ["s1"]


async def test_end_session_stamps_reason(pg_session: AsyncSession) -> None:
    repo = SessionRepo(pg_session)
    await repo.create_session(session_id="s1", source="cli")
    await repo.end_session("s1", end_reason="manual")
    meta = await repo.get_session_meta("s1")
    assert meta is not None
    assert meta["end_reason"] == "manual"
    assert meta["ended_at"] is not None


async def test_list_recent_orders_by_started_desc(
    pg_session: AsyncSession,
) -> None:
    repo = SessionRepo(pg_session)
    await repo.create_session(session_id="old", source="cli")
    await repo.create_session(session_id="new", source="cli")
    rows = await repo.list_recent_sessions(limit=10)
    assert [r["id"] for r in rows] == ["new", "old"]


async def test_list_recent_excludes_ended_when_asked(
    pg_session: AsyncSession,
) -> None:
    repo = SessionRepo(pg_session)
    await repo.create_session(session_id="live", source="cli")
    await repo.create_session(session_id="dead", source="cli")
    await repo.end_session("dead", end_reason="x")
    rows = await repo.list_recent_sessions(limit=10, include_ended=False)
    assert [r["id"] for r in rows] == ["live"]


async def test_delete_sessions_ended_before_sweeps_old(
    pg_session: AsyncSession,
) -> None:
    repo = SessionRepo(pg_session)
    await repo.create_session(session_id="a", source="cli")
    await repo.create_session(session_id="b", source="cli")
    await repo.end_session("a", end_reason="t")
    # Cutoff in the future ⇒ everything ended is old enough.
    deleted = await repo.delete_sessions_ended_before(time.time() + 60)
    assert deleted == 1
    rows = await repo.list_recent_sessions(limit=10)
    assert [r["id"] for r in rows] == ["b"]


async def test_find_session_by_prefix_unique(pg_session: AsyncSession) -> None:
    repo = SessionRepo(pg_session)
    await repo.create_session(session_id="abcdef", source="cli")
    assert await repo.find_session_by_prefix("abc") == "abcdef"
    assert await repo.find_session_by_prefix("xyz") is None


async def test_find_session_by_prefix_ambiguous(
    pg_session: AsyncSession,
) -> None:
    repo = SessionRepo(pg_session)
    await repo.create_session(session_id="abc-1", source="cli")
    await repo.create_session(session_id="abc-2", source="cli")
    with pytest.raises(ValueError, match="ambiguous"):
        await repo.find_session_by_prefix("abc")
