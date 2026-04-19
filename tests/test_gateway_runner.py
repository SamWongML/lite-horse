"""Tests for gateway runner dispatch + shutdown (Phase 9)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from lite_horse.config import Config
from lite_horse.gateway import runner as runner_mod
from lite_horse.gateway.guard import GuardRegistry
from lite_horse.gateway.runner import make_handler, run_gateway
from lite_horse.sessions.db import SessionDB


class _StubResult:
    def __init__(self, text: str) -> None:
        self.final_output = text


async def _stub_runner_run(agent: Any, text: str, **_kw: Any) -> _StubResult:
    # Echoes the prompt so tests can assert on concatenation behavior.
    return _StubResult(f"reply:{text}")


def _cfg() -> Config:
    return Config.model_validate(
        {
            "model": "gpt-test",
            "gateway": {
                "telegram": {"enabled": True, "allowed_user_ids": [1]},
            },
        }
    )


def _event(text: str, replies: list[str], *, chat_id: int = 42) -> dict[str, Any]:
    async def send_reply(msg: str) -> None:
        replies.append(msg)

    return {
        "platform": "telegram",
        "chat_type": "private",
        "chat_id": chat_id,
        "user_id": 1,
        "text": text,
        "is_command": False,
        "send_reply": send_reply,
    }


# ---------- make_handler ----------


@pytest.mark.asyncio
async def test_handler_runs_agent_and_sends_reply(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    monkeypatch.setattr(runner_mod.Runner, "run", _stub_runner_run)

    db = SessionDB()
    guards = GuardRegistry()
    handle = make_handler(db=db, guards=guards, cfg=_cfg())

    replies: list[str] = []
    await handle(_event("hello", replies))

    assert replies == ["reply:hello"]


@pytest.mark.asyncio
async def test_handler_queues_second_message_while_locked(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    gate = asyncio.Event()
    seen_prompts: list[str] = []

    async def gated_run(agent: Any, text: str, **_kw: Any) -> _StubResult:
        seen_prompts.append(text)
        await gate.wait()
        return _StubResult(f"reply:{text}")

    monkeypatch.setattr(runner_mod.Runner, "run", gated_run)

    db = SessionDB()
    guards = GuardRegistry()
    handle = make_handler(db=db, guards=guards, cfg=_cfg())

    replies: list[str] = []
    first = asyncio.create_task(handle(_event("one", replies)))
    # Yield so the first task acquires the lock before the second arrives.
    await asyncio.sleep(0)
    await handle(_event("two", replies))  # should queue, return immediately

    sk = "agent:main:telegram:private:42"
    guard = guards.get(sk)
    assert guard.pending == ["two"]
    assert guard.interrupt.is_set()
    assert replies == []

    gate.set()
    await first
    # Only the first run produced a reply; "two" is left in the queue until the
    # next send arrives (the v1 simple-queue contract).
    assert replies == ["reply:one"]
    assert seen_prompts == ["one"]


@pytest.mark.asyncio
async def test_handler_drains_pending_before_running(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    seen_prompts: list[str] = []

    async def recording_run(agent: Any, text: str, **_kw: Any) -> _StubResult:
        seen_prompts.append(text)
        return _StubResult(f"reply:{text}")

    monkeypatch.setattr(runner_mod.Runner, "run", recording_run)

    db = SessionDB()
    guards = GuardRegistry()
    handle = make_handler(db=db, guards=guards, cfg=_cfg())

    sk = "agent:main:telegram:private:42"
    guard = guards.get(sk)
    guard.pending.extend(["a", "b"])
    guard.interrupt.set()

    replies: list[str] = []
    await handle(_event("c", replies))

    assert seen_prompts == ["a\n\nb\n\nc"]
    assert replies == ["reply:a\n\nb\n\nc"]
    assert guard.pending == []
    assert not guard.interrupt.is_set()


@pytest.mark.asyncio
async def test_handler_sends_error_when_runner_fails(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home

    async def boom(agent: Any, text: str, **_kw: Any) -> _StubResult:
        raise RuntimeError("nope")

    monkeypatch.setattr(runner_mod.Runner, "run", boom)

    db = SessionDB()
    guards = GuardRegistry()
    handle = make_handler(db=db, guards=guards, cfg=_cfg())

    replies: list[str] = []
    await handle(_event("hi", replies))

    assert len(replies) == 1
    assert replies[0].startswith("⚠ error:")


# ---------- run_gateway guardrails ----------


@pytest.mark.asyncio
async def test_run_gateway_refuses_when_telegram_disabled(
    litehorse_home: Path,
) -> None:
    del litehorse_home
    with pytest.raises(SystemExit, match="disabled"):
        await run_gateway()


@pytest.mark.asyncio
async def test_run_gateway_refuses_with_empty_allowlist(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    (litehorse_home / "config.yaml").write_text(
        "gateway:\n  telegram:\n    enabled: true\n    allowed_user_ids: []\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="allowed_user_ids is empty"):
        await run_gateway()


@pytest.mark.asyncio
async def test_run_gateway_refuses_without_token(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    (litehorse_home / "config.yaml").write_text(
        "gateway:\n  telegram:\n    enabled: true\n    allowed_user_ids: [1]\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="TELEGRAM_BOT_TOKEN"):
        await run_gateway()


@pytest.mark.asyncio
async def test_run_gateway_writes_pid_and_cleans_up_on_stop(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    (litehorse_home / "config.yaml").write_text(
        "gateway:\n  telegram:\n    enabled: true\n    allowed_user_ids: [1]\n",
        encoding="utf-8",
    )

    pid_observed: list[bool] = []
    stopped: list[bool] = []

    class _StubAdapter:
        def __init__(
            self, *, token: str, allowed_user_ids: set[int], on_message: Any
        ) -> None:
            self.token = token
            self.allowed = allowed_user_ids
            self.on_message = on_message

        async def start(self) -> None:
            pid_observed.append((litehorse_home / "gateway.pid").exists())

        async def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr(runner_mod, "TelegramAdapter", _StubAdapter)

    # Short-circuit the signal-driven wait so the runner falls straight into
    # its cleanup block without us needing to raise a real signal.
    class _ImmediateEvent(asyncio.Event):
        async def wait(self) -> bool:
            return True

    monkeypatch.setattr(runner_mod.asyncio, "Event", _ImmediateEvent)

    await run_gateway()

    assert pid_observed == [True]
    assert stopped == [True]
    assert not (litehorse_home / "gateway.pid").exists()
