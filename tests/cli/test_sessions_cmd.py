"""Tests for `litehorse sessions {list, show, search, end, cleanup}`."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lite_horse.cli.commands.sessions import app
from lite_horse.cli.exit_codes import ExitCode
from lite_horse.sessions.local import LocalSessionRepo


def _seed_db(litehorse_home: Path, *, include_ended: bool = True) -> LocalSessionRepo:
    """Two sessions, one ended, one live, each with two messages."""
    db = LocalSessionRepo()
    db.create_session(session_id="sess-live", source="cli", model="gpt-5.0")
    db.append_message(session_id="sess-live", role="user", content="hello world")
    db.append_message(session_id="sess-live", role="assistant", content="hi friend")
    if include_ended:
        db.create_session(session_id="sess-old", source="web", model="gpt-5.0")
        db.append_message(session_id="sess-old", role="user", content="deploy me")
        db.append_message(session_id="sess-old", role="assistant", content="done")
        db.end_session("sess-old", end_reason="test")
    return db


def test_list_shows_recent_sessions(litehorse_home: Path) -> None:
    _seed_db(litehorse_home)
    runner = CliRunner()
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "sess-live" in result.stdout
    assert "sess-old" in result.stdout


def test_list_json_emits_ndjson_items_and_result(litehorse_home: Path) -> None:
    _seed_db(litehorse_home)
    runner = CliRunner()
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    records = [json.loads(line) for line in result.stdout.splitlines()]
    items = [r for r in records if r["kind"] == "item"]
    final = [r for r in records if r["kind"] == "result"]
    assert len(items) == 2
    assert final[0]["data"]["count"] == 2


def test_show_returns_transcript(litehorse_home: Path) -> None:
    _seed_db(litehorse_home)
    runner = CliRunner()
    result = runner.invoke(app, ["show", "sess-live", "--json"])
    assert result.exit_code == 0
    record = json.loads(result.stdout.splitlines()[0])
    assert record["data"]["id"] == "sess-live"
    assert len(record["data"]["messages"]) == 2


def test_show_missing_session_returns_not_found(litehorse_home: Path) -> None:
    _seed_db(litehorse_home)
    runner = CliRunner()
    result = runner.invoke(app, ["show", "does-not-exist"])
    assert result.exit_code == int(ExitCode.NOT_FOUND)


def test_search_fts5_finds_seeded_text(litehorse_home: Path) -> None:
    _seed_db(litehorse_home)
    runner = CliRunner()
    result = runner.invoke(app, ["search", "hello", "--json"])
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    items = [r for r in lines if r["kind"] == "item"]
    assert any("hello" in item["data"]["snippet"].lower() for item in items)


def test_end_stamps_end_reason(litehorse_home: Path) -> None:
    _seed_db(litehorse_home, include_ended=False)
    runner = CliRunner()
    result = runner.invoke(app, ["end", "sess-live", "--reason", "cli_test"])
    assert result.exit_code == 0
    db = LocalSessionRepo()
    rows = db.list_recent_sessions(limit=5)
    live_row = next(r for r in rows if r["id"] == "sess-live")
    assert live_row["end_reason"] == "cli_test"
    assert live_row["ended_at"] is not None


def test_end_missing_session_returns_not_found(litehorse_home: Path) -> None:
    _seed_db(litehorse_home, include_ended=False)
    runner = CliRunner()
    result = runner.invoke(app, ["end", "nope"])
    assert result.exit_code == int(ExitCode.NOT_FOUND)


def test_cleanup_removes_old_sessions(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _seed_db(litehorse_home)
    # Backdate the ended session to 40 days ago so --days 30 sweeps it.
    long_ago = time.time() - 40 * 86400
    db._conn.execute(
        "UPDATE sessions SET ended_at = ? WHERE id = ?", (long_ago, "sess-old")
    )
    db._conn.commit()
    runner = CliRunner()
    result = runner.invoke(app, ["cleanup", "--days", "30", "--yes"])
    assert result.exit_code == 0
    assert "deleted 1" in result.stdout.lower()
    rows = LocalSessionRepo().list_recent_sessions(limit=10)
    assert {r["id"] for r in rows} == {"sess-live"}


def test_cleanup_skips_live_sessions(litehorse_home: Path) -> None:
    _seed_db(litehorse_home, include_ended=False)
    runner = CliRunner()
    result = runner.invoke(app, ["cleanup", "--days", "0", "--yes"])
    assert result.exit_code == 0
    assert "deleted 0" in result.stdout.lower()
