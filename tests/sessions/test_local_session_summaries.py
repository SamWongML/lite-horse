""":class:`LocalSessionRepo` summarises sessions on REPL exit.

Covers the local SQLite parity store the cloud `session_summaries` table
mirrors, plus the ``summarize_on_exit`` helper that the REPL fires on
Ctrl-D / ``/exit``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lite_horse.agent import summarizer as summ_mod
from lite_horse.cli.repl.summarize_on_exit import summarize_on_exit
from lite_horse.sessions.local import LocalSessionRepo


class _FakeRunResult:
    def __init__(self, final_output: str) -> None:
        self.final_output = final_output


def _stub_runner(monkeypatch: pytest.MonkeyPatch, *, final_output: str) -> None:
    async def fake_run(*a: Any, **k: Any) -> _FakeRunResult:
        return _FakeRunResult(final_output=final_output)

    monkeypatch.setattr(summ_mod.Runner, "run", fake_run)


def test_upsert_and_list_recent(litehorse_home: Path) -> None:
    del litehorse_home
    db = LocalSessionRepo()
    db.create_session(session_id="s1", source="cli")
    db.create_session(session_id="s2", source="cli")
    db.upsert_summary(
        session_id="s1", topic="t1", summary="sum1", generator="gpt-x"
    )
    db.upsert_summary(
        session_id="s2", topic="t2", summary="sum2", generator="gpt-x"
    )
    rows = db.list_recent_summaries(limit=10)
    assert {r["session_id"] for r in rows} == {"s1", "s2"}

    # Re-upserting the same session updates in place.
    db.upsert_summary(
        session_id="s1", topic="t1!", summary="sum1!", generator="gpt-y"
    )
    fresh = db.get_summary("s1")
    assert fresh is not None
    assert fresh["topic"] == "t1!"
    assert fresh["summary"] == "sum1!"

    # Exclude filter works.
    rows = db.list_recent_summaries(exclude_session_id="s1", limit=10)
    assert [r["session_id"] for r in rows] == ["s2"]


@pytest.mark.asyncio
async def test_summarize_on_exit_writes_row(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    db = LocalSessionRepo()
    db.create_session(session_id="exit-1", source="cli")
    db.append_message(session_id="exit-1", role="user", content="hi")
    db.append_message(session_id="exit-1", role="assistant", content="hello")

    _stub_runner(
        monkeypatch,
        final_output='{"topic": "greeting", "summary": "user said hi, agent replied."}',
    )

    wrote = await summarize_on_exit(session_key="exit-1", model="gpt-test")
    assert wrote is True
    row = LocalSessionRepo().get_summary("exit-1")
    assert row is not None
    assert row["topic"] == "greeting"


@pytest.mark.asyncio
async def test_summarize_on_exit_noop_for_unknown_session(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    _stub_runner(monkeypatch, final_output='{"topic":"x","summary":"y"}')
    wrote = await summarize_on_exit(session_key="ghost", model="gpt-test")
    assert wrote is False


@pytest.mark.asyncio
async def test_summarize_on_exit_noop_for_empty_summary(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    db = LocalSessionRepo()
    db.create_session(session_id="empty-1", source="cli")
    db.append_message(session_id="empty-1", role="user", content="hi")
    _stub_runner(monkeypatch, final_output='{"topic": "", "summary": ""}')
    wrote = await summarize_on_exit(session_key="empty-1", model="gpt-test")
    assert wrote is False
    assert LocalSessionRepo().get_summary("empty-1") is None


@pytest.mark.asyncio
async def test_summarize_on_exit_swallows_side_agent_errors(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    db = LocalSessionRepo()
    db.create_session(session_id="boom-1", source="cli")
    db.append_message(session_id="boom-1", role="user", content="hi")

    async def fake_run(*a: Any, **k: Any) -> _FakeRunResult:
        raise RuntimeError("provider down")

    monkeypatch.setattr(summ_mod.Runner, "run", fake_run)
    wrote = await summarize_on_exit(session_key="boom-1", model="gpt-test")
    assert wrote is False
