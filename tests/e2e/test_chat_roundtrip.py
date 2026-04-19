"""End-to-end sanity tests for the Phase 8 chat REPL.

We stub out the OpenAI model call (``Runner.run``) so the test exercises the
wiring — session persistence, tool invocation via `memory_tool` /
`session_search`, and clean shutdown — without a network round-trip.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from rich.console import Console

from lite_horse import cli
from lite_horse.memory.store import MemoryStore
from lite_horse.sessions.db import SessionDB
from lite_horse.sessions.sdk_session import SDKSession
from lite_horse.sessions.search_tool import bind_db


class _FakeResult:
    def __init__(self, text: str) -> None:
        self.final_output = text


# ---------- CLI REPL roundtrip ----------


@pytest.fixture()
def stubbed_runner(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace ``Runner.run`` with a loopback that persists items to the session."""
    calls: list[dict[str, Any]] = []

    async def fake_run(
        agent: Any,
        user_in: str,
        *,
        session: Any,
        max_turns: int,
        **_: Any,
    ) -> _FakeResult:
        await session.add_items([{"role": "user", "content": user_in}])
        reply = f"echo: {user_in}"
        await session.add_items([{"role": "assistant", "content": reply}])
        calls.append({"input": user_in, "max_turns": max_turns})
        return _FakeResult(reply)

    monkeypatch.setattr(cli.Runner, "run", fake_run)
    return calls


def test_chat_persists_messages_and_honors_max_turns(
    litehorse_home: Path, stubbed_runner: list[dict[str, Any]]
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["chat", "--session-id", "e2e-persist"],
        input="hello there\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert "hello there" in stubbed_runner[0]["input"]
    # config default max_turns flows through from load_config.
    assert stubbed_runner[0]["max_turns"] == 90

    db = SessionDB(litehorse_home / "sessions.db")
    msgs = db.get_messages("e2e-persist")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hello there"
    assert msgs[1]["content"] == "echo: hello there"


def test_chat_resumes_existing_session(
    litehorse_home: Path, stubbed_runner: list[dict[str, Any]]
) -> None:
    runner = CliRunner()
    first = runner.invoke(
        cli.main,
        ["chat", "--session-id", "e2e-resume"],
        input="first\n/exit\n",
    )
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        cli.main,
        ["chat", "--session-id", "e2e-resume"],
        input="second\n/exit\n",
    )
    assert second.exit_code == 0, second.output

    db = SessionDB(litehorse_home / "sessions.db")
    msgs = db.get_messages("e2e-resume")
    assert [m["content"] for m in msgs] == [
        "first",
        "echo: first",
        "second",
        "echo: second",
    ]


def test_chat_blank_lines_and_errors_do_not_kill_repl(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    errors: list[str] = []
    good: list[str] = []

    async def flaky_run(
        agent: Any, user_in: str, *, session: Any, max_turns: int, **_: Any
    ) -> _FakeResult:
        if "boom" in user_in:
            raise RuntimeError("model exploded")
        await session.add_items([{"role": "user", "content": user_in}])
        good.append(user_in)
        return _FakeResult("ok")

    monkeypatch.setattr(cli.Runner, "run", flaky_run)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["chat", "--session-id", "e2e-errors"],
        input="\n  \nboom\nsurvives\n/exit\n",
    )
    errors.append(result.output)
    assert result.exit_code == 0, result.output
    # blank lines skipped, boom error surfaced, survives ran.
    assert good == ["survives"]
    assert "model exploded" in result.output


# ---------- Direct callable sanity: memory + session_search end-to-end ----------


def test_memory_and_session_search_survive_chat_sessions(litehorse_home: Path) -> None:
    """Phase 8 acceptance: memory and session_search survive across invocations.

    We invoke the underlying stores the way the tools do — exercising the same
    state dir + DB that ``cli._startup`` populates.
    """
    cli._startup()

    # memory.add writes USER block to disk.
    store = MemoryStore.for_user()
    store.add("prefers concise answers")
    user_md = (litehorse_home / "memories" / "USER.md").read_text(encoding="utf-8")
    assert "prefers concise answers" in user_md

    # session_search sees content written through SDKSession.add_items.
    db = cli._DB
    assert db is not None
    bind_db(db)
    session = SDKSession("search-seed", db, source="cli")
    asyncio.run(session.add_items([{"role": "user", "content": "concise answers please"}]))

    hits = db.search_messages("concise")
    assert hits and hits[0].session_id == "search-seed"
    assert "concise" in hits[0].snippet.lower()


# ---------- REPL loop unit test without CliRunner ----------


def test_repl_loop_exits_on_eof(litehorse_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Directly drive :func:`cli._repl_loop` to verify clean EOF handling."""
    del litehorse_home
    cli._startup()  # ensures bind_db for session_search

    async def fake_run(
        agent: Any, user_in: str, *, session: Any, max_turns: int, **_: Any
    ) -> _FakeResult:
        await session.add_items([{"role": "user", "content": user_in}])
        return _FakeResult("reply")

    monkeypatch.setattr(cli.Runner, "run", fake_run)

    lines = iter(["ping", ""])  # second yields EOF via StopIteration

    async def fake_input(prompt: str) -> str:
        del prompt
        try:
            return next(lines)
        except StopIteration as exc:
            raise EOFError from exc

    db = SessionDB()
    session = SDKSession("repl-unit", db, source="cli")
    asyncio.run(
        cli._repl_loop(
            session=session,
            agent=object(),
            max_turns=5,
            console=Console(file=_NullFile()),
            input_fn=fake_input,
        )
    )
    msgs = db.get_messages("repl-unit")
    assert msgs and msgs[0]["content"] == "ping"


class _NullFile:
    """Silences Rich output during tests."""

    def write(self, _: str) -> int:
        return 0

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False
