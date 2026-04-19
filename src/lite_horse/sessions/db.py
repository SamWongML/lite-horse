"""SessionDB — SQLite + FTS5 store for lite-horse sessions and messages.

WAL journal + ``BEGIN IMMEDIATE`` retry with jitter for write contention, plus
FTS5 triggers that mirror ``messages.content`` into a searchable virtual table.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from lite_horse.constants import SCHEMA_VERSION, litehorse_home

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    model TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    title TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_title_unique
    ON sessions(title) WHERE title IS NOT NULL;

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content=messages, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
"""

_WRITE_MAX_RETRIES = 15
_WRITE_RETRY_MIN_S = 0.020
_WRITE_RETRY_MAX_S = 0.150
_CHECKPOINT_EVERY_N_WRITES = 50

_HYPHEN_TERM_RE = re.compile(r"\b(\w+(?:-\w+)+)\b")
_TRAILING_BOOL_RE = re.compile(r"\s+(AND|OR|NOT)\s*$", re.IGNORECASE)


@dataclass
class SearchHit:
    id: int
    session_id: str
    role: str
    timestamp: float
    snippet: str
    source: str


class SessionDB:
    """Thin SQLite wrapper. One instance per process; safe for multiple threads."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (litehorse_home() / "sessions.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._writes_since_checkpoint = 0
        self._init_schema()

    # ---------- connection management ----------
    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self.db_path), timeout=1.0, check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    def _init_schema(self) -> None:
        # ``executescript`` issues an implicit COMMIT before running, so we must
        # not wrap it in ``_writer()`` (which has already begun a transaction).
        c = self._conn()
        c.executescript(_SCHEMA)
        row = c.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            with self._writer() as wc:
                wc.execute(
                    "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
                )

    # ---------- write contention helper ----------
    def _writer(self) -> _Writer:
        """Return a context manager that BEGIN IMMEDIATEs with retry + jitter."""
        return _Writer(self)

    def _maybe_checkpoint(self) -> None:
        self._writes_since_checkpoint += 1
        if self._writes_since_checkpoint >= _CHECKPOINT_EVERY_N_WRITES:
            try:
                self._conn().execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.OperationalError:
                pass
            self._writes_since_checkpoint = 0

    # ---------- session ops ----------
    def create_session(
        self,
        *,
        session_id: str,
        source: str,
        model: str | None = None,
        user_id: str | None = None,
    ) -> None:
        with self._writer() as c:
            c.execute(
                "INSERT OR IGNORE INTO sessions(id, source, user_id, model, started_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, source, user_id, model, time.time()),
            )
        self._maybe_checkpoint()

    def end_session(self, session_id: str, *, end_reason: str = "user_exit") -> None:
        with self._writer() as c:
            c.execute(
                "UPDATE sessions SET ended_at=?, end_reason=? WHERE id=?",
                (time.time(), end_reason, session_id),
            )

    # ---------- message ops ----------
    def append_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str | None,
        tool_call_id: str | None = None,
        tool_calls: Iterable[dict[str, Any]] | None = None,
        tool_name: str | None = None,
        token_count: int | None = None,
    ) -> int:
        tool_calls_json = json.dumps(list(tool_calls)) if tool_calls else None
        with self._writer() as c:
            cur = c.execute(
                "INSERT INTO messages(session_id, role, content, tool_call_id, tool_calls, "
                "tool_name, timestamp, token_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    content,
                    tool_call_id,
                    tool_calls_json,
                    tool_name,
                    time.time(),
                    token_count,
                ),
            )
            c.execute(
                "UPDATE sessions SET message_count = message_count + 1 WHERE id=?",
                (session_id,),
            )
            row_id = int(cur.lastrowid or 0)
        self._maybe_checkpoint()
        return row_id

    def get_messages(
        self, session_id: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Return messages in chronological order.

        If ``limit`` is given, returns the **latest** ``limit`` messages still
        ordered earliest-first (matching the SDK ``Session.get_items`` contract).
        """
        if limit is None:
            rows = self._conn().execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY timestamp, id",
                (session_id,),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM ("
                "  SELECT * FROM messages WHERE session_id=? "
                "  ORDER BY timestamp DESC, id DESC LIMIT ?"
                ") ORDER BY timestamp, id",
                (session_id, int(limit)),
            ).fetchall()
        return [self._row_to_msg(r) for r in rows]

    def pop_last_message(self, session_id: str) -> dict[str, Any] | None:
        with self._writer() as c:
            row = c.execute(
                "SELECT * FROM messages WHERE session_id=? "
                "ORDER BY timestamp DESC, id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            c.execute("DELETE FROM messages WHERE id=?", (row["id"],))
            c.execute(
                "UPDATE sessions SET message_count = MAX(message_count - 1, 0) WHERE id=?",
                (session_id,),
            )
            return self._row_to_msg(row)

    def clear_session(self, session_id: str) -> None:
        with self._writer() as c:
            c.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            c.execute("UPDATE sessions SET message_count=0 WHERE id=?", (session_id,))

    @staticmethod
    def _row_to_msg(r: sqlite3.Row) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": r["role"]}
        if r["content"] is not None:
            msg["content"] = r["content"]
        if r["tool_call_id"]:
            msg["tool_call_id"] = r["tool_call_id"]
        if r["tool_calls"]:
            msg["tool_calls"] = json.loads(r["tool_calls"])
        if r["tool_name"]:
            msg["name"] = r["tool_name"]
        return msg

    # ---------- FTS5 search ----------
    def search_messages(
        self,
        query: str,
        *,
        limit: int = 20,
        source_filter: list[str] | None = None,
        exclude_sources: list[str] | None = None,
        role_filter: list[str] | None = None,
    ) -> list[SearchHit]:
        sanitized = self._sanitize_fts5_query(query)
        if not sanitized:
            return []
        params: list[Any] = [sanitized]
        sql = (
            "SELECT m.id AS id, m.session_id AS session_id, m.role AS role, "
            "m.timestamp AS timestamp, "
            "snippet(messages_fts, 0, '>>>', '<<<', '...', 8) AS snippet, "
            "s.source AS source "
            "FROM messages_fts "
            "JOIN messages m ON m.id = messages_fts.rowid "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE messages_fts MATCH ?"
        )
        if source_filter:
            sql += f" AND s.source IN ({','.join('?' for _ in source_filter)})"
            params.extend(source_filter)
        if exclude_sources:
            sql += f" AND s.source NOT IN ({','.join('?' for _ in exclude_sources)})"
            params.extend(exclude_sources)
        if role_filter:
            sql += f" AND m.role IN ({','.join('?' for _ in role_filter)})"
            params.extend(role_filter)
        sql += " ORDER BY m.timestamp DESC LIMIT ?"
        params.append(int(limit))
        rows = self._conn().execute(sql, params).fetchall()
        return [
            SearchHit(
                id=int(r["id"]),
                session_id=str(r["session_id"]),
                role=str(r["role"]),
                timestamp=float(r["timestamp"]),
                snippet=str(r["snippet"]),
                source=str(r["source"]),
            )
            for r in rows
        ]

    @staticmethod
    def _sanitize_fts5_query(q: str) -> str:
        """Make a user-entered query safe for FTS5 MATCH.

        - Drop unmatched double-quotes (raises syntax error otherwise).
        - Wrap hyphenated terms in quotes so FTS5 treats them as phrases.
        - Strip trailing boolean operators (AND/OR/NOT with nothing after).
        """
        q = q.strip()
        if not q:
            return ""
        if q.count('"') % 2 == 1:
            q = q.replace('"', "")
        q = _HYPHEN_TERM_RE.sub(r'"\1"', q)
        q = _TRAILING_BOOL_RE.sub("", q)
        return q.strip()


class _Writer:
    """``BEGIN IMMEDIATE`` context manager with retry + jitter on ``database is locked``."""

    def __init__(self, db: SessionDB) -> None:
        self.db = db
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        last_err: Exception | None = None
        for _ in range(_WRITE_MAX_RETRIES):
            conn = self.db._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as e:
                last_err = e
                if "database is locked" not in str(e).lower():
                    raise
                time.sleep(random.uniform(_WRITE_RETRY_MIN_S, _WRITE_RETRY_MAX_S))
                continue
            self.conn = conn
            return conn
        raise RuntimeError(f"SessionDB write contention exceeded retries: {last_err}")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self.conn is not None
        if exc is not None:
            self.conn.execute("ROLLBACK")
        else:
            self.conn.execute("COMMIT")
