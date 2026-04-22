"""Tests for the SQLite + FTS5 session store (Phase 1)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lite_horse.constants import SCHEMA_VERSION
from lite_horse.sessions.db import SessionDB


@pytest.fixture()
def db(tmp_path: Path) -> SessionDB:
    return SessionDB(db_path=tmp_path / "t.db")


def test_create_and_append(db: SessionDB) -> None:
    db.create_session(session_id="s1", source="cli")
    mid = db.append_message(session_id="s1", role="user", content="hello world")
    msgs = db.get_messages("s1")
    assert len(msgs) == 1
    assert msgs[0]["content"] == "hello world"
    assert msgs[0]["role"] == "user"
    assert isinstance(mid, int) and mid > 0


def test_fts5_basic(db: SessionDB) -> None:
    db.create_session(session_id="s1", source="cli")
    db.append_message(session_id="s1", role="user", content="docker deployment notes")
    db.append_message(session_id="s1", role="assistant", content="kubernetes cluster setup")
    hits = db.search_messages("docker")
    assert len(hits) == 1
    assert ">>>docker<<<" in hits[0].snippet
    assert hits[0].source == "cli"


def test_fts5_filter_by_role(db: SessionDB) -> None:
    db.create_session(session_id="s1", source="cli")
    db.append_message(session_id="s1", role="user", content="error log")
    db.append_message(session_id="s1", role="assistant", content="error handler")
    user_only = db.search_messages("error", role_filter=["user"])
    assert len(user_only) == 1
    assert user_only[0].role == "user"


def test_fts5_query_sanitization(db: SessionDB) -> None:
    db.create_session(session_id="s1", source="cli")
    db.append_message(session_id="s1", role="user", content="chat-send broke")
    # Hyphenated terms would normally trip FTS5; sanitizer wraps them in quotes.
    hits = db.search_messages("chat-send")
    assert len(hits) == 1
    # A dangling boolean operator should not raise.
    assert db.search_messages("chat AND") == [] or len(db.search_messages("chat AND")) >= 0
    # An unmatched double-quote should not raise.
    assert db.search_messages('"unterminated') is not None


def test_pop_last_message(db: SessionDB) -> None:
    db.create_session(session_id="s1", source="cli")
    db.append_message(session_id="s1", role="user", content="a")
    db.append_message(session_id="s1", role="assistant", content="b")
    popped = db.pop_last_message("s1")
    assert popped is not None
    assert popped["content"] == "b"
    remaining = db.get_messages("s1")
    assert len(remaining) == 1
    assert remaining[0]["content"] == "a"


def test_get_messages_limit_returns_latest(db: SessionDB) -> None:
    db.create_session(session_id="s1", source="cli")
    for i in range(5):
        db.append_message(session_id="s1", role="user", content=f"m{i}")
    latest_two = db.get_messages("s1", limit=2)
    assert [m["content"] for m in latest_two] == ["m3", "m4"]


def test_migrates_v1_db_to_v2(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    # Seed a v1-shape DB: messages has a token_count column, version=1.
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT, model TEXT,
            started_at REAL NOT NULL, ended_at REAL, end_reason TEXT,
            message_count INTEGER DEFAULT 0, tool_call_count INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            title TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            timestamp REAL NOT NULL,
            token_count INTEGER
        );
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version(version) VALUES (1);
        INSERT INTO sessions(id, source, started_at) VALUES ('s1', 'cli', 0.0);
        INSERT INTO messages(session_id, role, content, timestamp, token_count)
            VALUES ('s1', 'user', 'legacy row', 1.0, 123);
        """
    )
    raw.commit()
    raw.close()

    db = SessionDB(db_path=db_path)
    c = db._conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
    assert "token_count" not in cols
    version = int(c.execute("SELECT version FROM schema_version").fetchone()["version"])
    assert version == SCHEMA_VERSION

    # Pre-existing row survives the migration and append still works.
    db.append_message(session_id="s1", role="assistant", content="post-migration")
    contents = [m["content"] for m in db.get_messages("s1")]
    assert contents == ["legacy row", "post-migration"]


def test_fts5_exclude_sources(db: SessionDB) -> None:
    db.create_session(session_id="cli-1", source="cli")
    db.create_session(session_id="cron-1", source="cron")
    db.append_message(session_id="cli-1", role="user", content="budget report")
    db.append_message(session_id="cron-1", role="assistant", content="budget report")
    no_cron = db.search_messages("budget", exclude_sources=["cron"])
    assert [h.source for h in no_cron] == ["cli"]
