"""Local SQLite + FTS5 session repository for the dev REPL / single-user CLI.

This is the v0.4 successor to the deleted ``sessions.db.SessionDB``: same
public surface (so api / cron / evolve / cli call sites are unchanged) but
implemented as a slim wrapper without the v0.3 contention scaffolding —
no per-thread connection cache, no ``BEGIN IMMEDIATE`` retry loop, no
WAL-checkpoint pacing, no in-place schema migrator. The cloud path uses
:class:`lite_horse.repositories.SessionRepo` /
:class:`MessageRepo`; this file stays sync-only and is consumed by code
that historically held a ``SessionDB`` instance.

Concurrency model: one connection per :class:`LocalSessionRepo` instance,
guarded by a :class:`threading.RLock` so multi-thread writers serialize
in-process. WAL journal mode keeps readers (incl. cross-process) lock-free.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from lite_horse.constants import litehorse_home
from lite_horse.sessions.types import SearchHit

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
    timestamp REAL NOT NULL
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
"""

_HYPHEN_TERM_RE = re.compile(r"\b(\w+(?:-\w+)+)\b")
_TRAILING_BOOL_RE = re.compile(r"\s+(AND|OR|NOT)\s*$", re.IGNORECASE)


def _fts5_match(q: str) -> str:
    """Coerce a user-entered query into a syntactically valid FTS5 MATCH.

    Mirrors the leniency of Postgres' ``websearch_to_tsquery`` enough that
    the FTS-parity test holds: drop unmatched quotes, wrap hyphenated
    tokens as phrases, strip a dangling trailing boolean. Anything we
    can't parse cleanly is just dropped — the caller falls back to an
    empty result rather than raising.
    """
    q = q.strip()
    if not q:
        return ""
    if q.count('"') % 2 == 1:
        q = q.replace('"', "")
    q = _HYPHEN_TERM_RE.sub(r'"\1"', q)
    q = _TRAILING_BOOL_RE.sub("", q)
    return q.strip()


class LocalSessionRepo:
    """SQLite + FTS5 session/message store — single-user dev/REPL path."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (litehorse_home() / "sessions.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), timeout=5.0, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------- session ops ----------
    def create_session(
        self,
        *,
        session_id: str,
        source: str,
        model: str | None = None,
        user_id: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions(id, source, user_id, model, started_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, source, user_id, model, time.time()),
            )
            self._conn.commit()

    def end_session(self, session_id: str, *, end_reason: str = "user_exit") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET ended_at=?, end_reason=? WHERE id=?",
                (time.time(), end_reason, session_id),
            )
            self._conn.commit()

    def get_session_meta(self, session_id: str) -> dict[str, Any] | None:
        """Return one session row's metadata. Replaces v0.3 ``_conn().execute``
        peeks from ``evolve.trace_miner``."""
        row = self._conn.execute(
            "SELECT id, source, user_id, model, started_at, ended_at, end_reason, "
            "message_count, tool_call_count, input_tokens, output_tokens, title "
            "FROM sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return _session_row_to_dict(row)

    def list_recent_sessions(
        self, *, limit: int = 20, include_ended: bool = True
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, source, user_id, model, started_at, ended_at, end_reason, "
            "message_count, tool_call_count, input_tokens, output_tokens, title "
            "FROM sessions"
        )
        if not include_ended:
            sql += " WHERE ended_at IS NULL"
        sql += " ORDER BY started_at DESC LIMIT ?"
        rows = self._conn.execute(sql, (int(limit),)).fetchall()
        return [_session_row_to_dict(r) for r in rows]

    def delete_sessions_ended_before(self, cutoff_ts: float) -> int:
        with self._lock:
            ids = [
                str(r["id"])
                for r in self._conn.execute(
                    "SELECT id FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?",
                    (float(cutoff_ts),),
                ).fetchall()
            ]
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            self._conn.execute(
                f"DELETE FROM messages WHERE session_id IN ({placeholders})", ids
            )
            self._conn.execute(
                f"DELETE FROM sessions WHERE id IN ({placeholders})", ids
            )
            self._conn.commit()
            return len(ids)

    def find_session_by_prefix(self, prefix: str) -> str | None:
        if not prefix:
            return None
        rows = self._conn.execute(
            "SELECT id FROM sessions WHERE id LIKE ? ORDER BY started_at DESC LIMIT 2",
            (f"{prefix}%",),
        ).fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(f"ambiguous session prefix: {prefix!r}")
        return str(rows[0]["id"])

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
    ) -> int:
        tool_calls_json = json.dumps(list(tool_calls)) if tool_calls else None
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(session_id, role, content, tool_call_id, "
                "tool_calls, tool_name, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    content,
                    tool_call_id,
                    tool_calls_json,
                    tool_name,
                    time.time(),
                ),
            )
            self._conn.execute(
                "UPDATE sessions SET message_count = message_count + 1 WHERE id=?",
                (session_id,),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def get_messages(
        self, session_id: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        if limit is None:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY timestamp, id",
                (session_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM ("
                "  SELECT * FROM messages WHERE session_id=? "
                "  ORDER BY timestamp DESC, id DESC LIMIT ?"
                ") ORDER BY timestamp, id",
                (session_id, int(limit)),
            ).fetchall()
        return [_message_row_to_msg(r) for r in rows]

    def pop_last_message(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE session_id=? "
                "ORDER BY timestamp DESC, id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute("DELETE FROM messages WHERE id=?", (row["id"],))
            self._conn.execute(
                "UPDATE sessions SET message_count = MAX(message_count - 1, 0) WHERE id=?",
                (session_id,),
            )
            self._conn.commit()
            return _message_row_to_msg(row)

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM messages WHERE session_id=?", (session_id,)
            )
            self._conn.execute(
                "UPDATE sessions SET message_count=0 WHERE id=?", (session_id,)
            )
            self._conn.commit()

    def copy_messages(self, *, src_session_id: str, dst_session_id: str) -> int:
        """Copy every message from ``src`` to ``dst``. Returns count copied."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, tool_call_id, tool_calls, tool_name, timestamp "
                "FROM messages WHERE session_id=? ORDER BY timestamp, id",
                (src_session_id,),
            ).fetchall()
            for r in rows:
                self._conn.execute(
                    "INSERT INTO messages(session_id, role, content, tool_call_id, "
                    "tool_calls, tool_name, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        dst_session_id,
                        r["role"],
                        r["content"],
                        r["tool_call_id"],
                        r["tool_calls"],
                        r["tool_name"],
                        r["timestamp"],
                    ),
                )
            if rows:
                self._conn.execute(
                    "UPDATE sessions SET message_count=? WHERE id=?",
                    (len(rows), dst_session_id),
                )
            self._conn.commit()
            return len(rows)

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
        match_query = _fts5_match(query)
        if not match_query:
            return []
        params: list[Any] = [match_query]
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
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # Anything that slipped past _fts5_match (e.g. a lone NEAR()
            # call) is treated as "no hits" rather than a 500.
            return []
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


def _message_row_to_msg(r: sqlite3.Row) -> dict[str, Any]:
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


def _session_row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "source": str(r["source"]),
        "user_id": r["user_id"],
        "model": r["model"],
        "started_at": float(r["started_at"]),
        "ended_at": r["ended_at"],
        "end_reason": r["end_reason"],
        "message_count": int(r["message_count"] or 0),
        "tool_call_count": int(r["tool_call_count"] or 0),
        "input_tokens": int(r["input_tokens"] or 0),
        "output_tokens": int(r["output_tokens"] or 0),
        "title": r["title"],
    }


__all__ = ["LocalSessionRepo", "SearchHit"]
