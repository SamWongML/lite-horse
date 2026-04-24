"""Dispatch-level tests for the root CLI group.

We don't exercise full subcommand bodies here (those live in each command's
own test module). We just verify that:
  * bare `litehorse` routes to the `repl` command
  * `litehorse <prompt>` routes to the same command in one-shot mode
  * named subcommands are reachable via `--help`
"""
from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from lite_horse.cli import app as cli_app
from lite_horse.cli.repl import loop as repl_loop


def test_help_lists_all_registered_subcommands() -> None:
    cli_app._attach_typer_commands()
    runner = CliRunner()
    result = runner.invoke(cli_app.cli, ["--help"])
    assert result.exit_code == 0
    for name in ("repl", "version", "doctor", "config", "completion", "debug"):
        assert name in result.output


def _stub_main_loop(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def fake_main_loop(
        *,
        prompt: str | None = None,
        stdin_text: str | None = None,
        session_key: str | None = None,
    ) -> int:
        seen["prompt"] = prompt
        seen["stdin_text"] = stdin_text
        seen["session_key"] = session_key
        return 0

    monkeypatch.setattr(repl_loop, "main_loop", fake_main_loop)
    return seen


def test_bare_invocation_routes_to_repl(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub_main_loop(monkeypatch)
    result = CliRunner().invoke(cli_app.cli, [])
    assert result.exit_code == 0
    assert seen == {"prompt": None, "stdin_text": None, "session_key": None}


def test_positional_prompt_routes_to_repl_one_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _stub_main_loop(monkeypatch)
    result = CliRunner().invoke(cli_app.cli, ["write", "a", "haiku"])
    assert result.exit_code == 0
    assert seen["prompt"] == "write a haiku"


def test_session_flag_threads_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub_main_loop(monkeypatch)
    result = CliRunner().invoke(cli_app.cli, ["--session", "agent:cli:repl:local"])
    assert result.exit_code == 0
    assert seen["session_key"] == "agent:cli:repl:local"


