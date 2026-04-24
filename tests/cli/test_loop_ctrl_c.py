"""Two-press Ctrl-C unit tests — drives the pure handler factory.

We don't raise real signals; we call the handler directly and inspect the
recorded state. Integration with the asyncio loop is exercised by the
pexpect smoke test in commit 4.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from lite_horse.cli.repl import loop as loop_mod
from lite_horse.cli.repl.loop import (
    CTRL_C_EXIT_WINDOW_S,
    _CtrlCState,
    _make_sigint_handler,
)


class _FakeTask:
    def __init__(self) -> None:
        self.cancelled = 0
        self._done = False

    def done(self) -> bool:
        return self._done

    def cancel(self) -> bool:
        self.cancelled += 1
        return True

    def finish(self) -> None:
        self._done = True


def _make(*, now_seq: list[float]) -> tuple[_CtrlCState, _FakeTask, Any]:
    cc = _CtrlCState()
    task = _FakeTask()
    it = iter(now_seq)
    handler = _make_sigint_handler(cc, task, now=lambda: next(it))  # type: ignore[arg-type]
    return cc, task, handler


def test_first_press_cancels_no_exit() -> None:
    cc, task, handler = _make(now_seq=[10.0])
    handler()
    assert task.cancelled == 1
    assert cc.requested_exit is False
    assert cc.last_press == 10.0


def test_two_quick_presses_request_exit() -> None:
    cc, task, handler = _make(now_seq=[10.0, 10.5])
    handler()
    handler()
    assert task.cancelled == 2
    assert cc.requested_exit is True


def test_two_slow_presses_do_not_exit() -> None:
    cc, _task, handler = _make(now_seq=[10.0, 10.0 + CTRL_C_EXIT_WINDOW_S + 0.1])
    handler()
    handler()
    assert cc.requested_exit is False


def test_handler_skips_cancel_when_task_done() -> None:
    cc, task, handler = _make(now_seq=[10.0])
    task.finish()
    handler()
    assert task.cancelled == 0
    assert cc.requested_exit is False


@pytest.mark.asyncio
async def test_await_with_two_press_returns_false_on_clean_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: turn completes, no Ctrl-C, helper returns False."""

    async def fake_turn(state: Any, text: str) -> None:
        del state, text
        await asyncio.sleep(0)

    monkeypatch.setattr(loop_mod, "_run_one_turn", fake_turn)
    state = loop_mod.ReplState(session_key="k")
    requested_exit = await loop_mod._await_with_two_press_ctrl_c(state, "hi")
    assert requested_exit is False
    assert state.current_turn_task is None
