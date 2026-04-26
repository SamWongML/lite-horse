"""Tenant-scoped Postgres message repository tests."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.repositories import MessageRepo, SessionRepo

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _seed(pg_session: AsyncSession) -> tuple[SessionRepo, MessageRepo]:
    sr = SessionRepo(pg_session)
    mr = MessageRepo(pg_session)
    await sr.create_session(session_id="s1", source="cli")
    return sr, mr


async def test_append_then_get_messages(pg_session: AsyncSession) -> None:
    _, mr = await _seed(pg_session)
    rid = await mr.append_message(
        session_id="s1", role="user", content="hello world"
    )
    assert isinstance(rid, int) and rid > 0
    msgs = await mr.get_messages("s1")
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hello world"


async def test_append_increments_message_count(pg_session: AsyncSession) -> None:
    sr, mr = await _seed(pg_session)
    await mr.append_message(session_id="s1", role="user", content="m1")
    await mr.append_message(session_id="s1", role="assistant", content="m2")
    meta = await sr.get_session_meta("s1")
    assert meta is not None
    assert meta["message_count"] == 2


async def test_get_messages_limit_returns_latest(
    pg_session: AsyncSession,
) -> None:
    _, mr = await _seed(pg_session)
    for i in range(5):
        await mr.append_message(session_id="s1", role="user", content=f"m{i}")
    latest = await mr.get_messages("s1", limit=2)
    assert [m["content"] for m in latest] == ["m3", "m4"]


async def test_pop_last_message(pg_session: AsyncSession) -> None:
    sr, mr = await _seed(pg_session)
    await mr.append_message(session_id="s1", role="user", content="a")
    await mr.append_message(session_id="s1", role="assistant", content="b")
    popped = await mr.pop_last_message("s1")
    assert popped is not None
    assert popped["content"] == "b"
    remaining = await mr.get_messages("s1")
    assert [m["content"] for m in remaining] == ["a"]
    meta = await sr.get_session_meta("s1")
    assert meta is not None
    assert meta["message_count"] == 1


async def test_pop_last_on_empty_returns_none(pg_session: AsyncSession) -> None:
    _, mr = await _seed(pg_session)
    assert await mr.pop_last_message("s1") is None


async def test_clear_session_drops_all(pg_session: AsyncSession) -> None:
    sr, mr = await _seed(pg_session)
    await mr.append_message(session_id="s1", role="user", content="a")
    await mr.append_message(session_id="s1", role="assistant", content="b")
    await mr.clear_session("s1")
    assert await mr.get_messages("s1") == []
    meta = await sr.get_session_meta("s1")
    assert meta is not None
    assert meta["message_count"] == 0


async def test_copy_messages_preserves_order(pg_session: AsyncSession) -> None:
    sr, mr = await _seed(pg_session)
    await sr.create_session(session_id="dst", source="cli")
    await mr.append_message(session_id="s1", role="user", content="m0")
    await mr.append_message(session_id="s1", role="assistant", content="m1")
    n = await mr.copy_messages(src_session_id="s1", dst_session_id="dst")
    assert n == 2
    contents = [m["content"] for m in await mr.get_messages("dst")]
    assert contents == ["m0", "m1"]


async def test_search_messages_basic(pg_session: AsyncSession) -> None:
    _, mr = await _seed(pg_session)
    await mr.append_message(
        session_id="s1", role="user", content="docker deployment notes"
    )
    await mr.append_message(
        session_id="s1", role="assistant", content="kubernetes cluster setup"
    )
    hits = await mr.search_messages("docker")
    assert len(hits) == 1
    assert ">>>docker<<<" in hits[0].snippet
    assert hits[0].source == "cli"


async def test_search_messages_role_filter(pg_session: AsyncSession) -> None:
    _, mr = await _seed(pg_session)
    await mr.append_message(session_id="s1", role="user", content="error log")
    await mr.append_message(session_id="s1", role="assistant", content="error handler")
    user_only = await mr.search_messages("error", role_filter=["user"])
    assert len(user_only) == 1
    assert user_only[0].role == "user"


async def test_search_messages_exclude_sources(pg_session: AsyncSession) -> None:
    sr, mr = await _seed(pg_session)
    await sr.create_session(session_id="cron-1", source="cron")
    await mr.append_message(
        session_id="s1", role="user", content="budget report"
    )
    await mr.append_message(
        session_id="cron-1", role="assistant", content="budget report"
    )
    hits = await mr.search_messages("budget", exclude_sources=["cron"])
    assert [h.source for h in hits] == ["cli"]


async def test_search_messages_empty_query_returns_empty(
    pg_session: AsyncSession,
) -> None:
    _, mr = await _seed(pg_session)
    await mr.append_message(session_id="s1", role="user", content="anything")
    assert await mr.search_messages("") == []
    assert await mr.search_messages("   ") == []
