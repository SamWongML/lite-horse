"""Tests for the SQLite + FTS5 dev session store."""
from __future__ import annotations

from pathlib import Path

import pytest

from lite_horse.sessions.local import LocalSessionRepo


@pytest.fixture()
def db(tmp_path: Path) -> LocalSessionRepo:
    return LocalSessionRepo(db_path=tmp_path / "t.db")


def test_create_and_append(db: LocalSessionRepo) -> None:
    db.create_session(session_id="s1", source="cli")
    mid = db.append_message(session_id="s1", role="user", content="hello world")
    msgs = db.get_messages("s1")
    assert len(msgs) == 1
    assert msgs[0]["content"] == "hello world"
    assert msgs[0]["role"] == "user"
    assert isinstance(mid, int) and mid > 0


def test_fts5_basic(db: LocalSessionRepo) -> None:
    db.create_session(session_id="s1", source="cli")
    db.append_message(session_id="s1", role="user", content="docker deployment notes")
    db.append_message(session_id="s1", role="assistant", content="kubernetes cluster setup")
    hits = db.search_messages("docker")
    assert len(hits) == 1
    assert ">>>docker<<<" in hits[0].snippet
    assert hits[0].source == "cli"


def test_fts5_filter_by_role(db: LocalSessionRepo) -> None:
    db.create_session(session_id="s1", source="cli")
    db.append_message(session_id="s1", role="user", content="error log")
    db.append_message(session_id="s1", role="assistant", content="error handler")
    user_only = db.search_messages("error", role_filter=["user"])
    assert len(user_only) == 1
    assert user_only[0].role == "user"


def test_fts5_query_lenient(db: LocalSessionRepo) -> None:
    db.create_session(session_id="s1", source="cli")
    db.append_message(session_id="s1", role="user", content="chat-send broke")
    # Hyphenated terms shouldn't trip FTS5.
    hits = db.search_messages("chat-send")
    assert len(hits) == 1
    # A dangling boolean operator is dropped, not raised.
    assert db.search_messages("chat AND") is not None
    # An unmatched double-quote falls through to a safe empty/match.
    assert db.search_messages('"unterminated') is not None


def test_pop_last_message(db: LocalSessionRepo) -> None:
    db.create_session(session_id="s1", source="cli")
    db.append_message(session_id="s1", role="user", content="a")
    db.append_message(session_id="s1", role="assistant", content="b")
    popped = db.pop_last_message("s1")
    assert popped is not None
    assert popped["content"] == "b"
    remaining = db.get_messages("s1")
    assert len(remaining) == 1
    assert remaining[0]["content"] == "a"


def test_get_messages_limit_returns_latest(db: LocalSessionRepo) -> None:
    db.create_session(session_id="s1", source="cli")
    for i in range(5):
        db.append_message(session_id="s1", role="user", content=f"m{i}")
    latest_two = db.get_messages("s1", limit=2)
    assert [m["content"] for m in latest_two] == ["m3", "m4"]


def test_fts5_exclude_sources(db: LocalSessionRepo) -> None:
    db.create_session(session_id="cli-1", source="cli")
    db.create_session(session_id="cron-1", source="cron")
    db.append_message(session_id="cli-1", role="user", content="budget report")
    db.append_message(session_id="cron-1", role="assistant", content="budget report")
    no_cron = db.search_messages("budget", exclude_sources=["cron"])
    assert [h.source for h in no_cron] == ["cli"]


def test_get_session_meta_returns_dict(db: LocalSessionRepo) -> None:
    db.create_session(session_id="s1", source="cli", model="gpt-test")
    db.end_session("s1", end_reason="user_exit")
    meta = db.get_session_meta("s1")
    assert meta is not None
    assert meta["id"] == "s1"
    assert meta["source"] == "cli"
    assert meta["end_reason"] == "user_exit"
    assert meta["ended_at"] is not None


def test_copy_messages_preserves_order(db: LocalSessionRepo) -> None:
    db.create_session(session_id="src", source="cli")
    db.create_session(session_id="dst", source="cli")
    db.append_message(session_id="src", role="user", content="m0")
    db.append_message(session_id="src", role="assistant", content="m1")
    n = db.copy_messages(src_session_id="src", dst_session_id="dst")
    assert n == 2
    contents = [m["content"] for m in db.get_messages("dst")]
    assert contents == ["m0", "m1"]
