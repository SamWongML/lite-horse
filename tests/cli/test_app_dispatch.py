"""Dispatch-level tests for the root CLI group.

We don't exercise full subcommand bodies here (those live in each command's
own test module). We just verify that:
  * bare `litehorse` routes to the `repl` stub
  * named subcommands are reachable
  * `--help` emits the expected top-level commands
"""
from __future__ import annotations

from click.testing import CliRunner

from lite_horse.cli import app as cli_app


def test_help_lists_all_registered_subcommands() -> None:
    cli_app._attach_typer_commands()
    runner = CliRunner()
    result = runner.invoke(cli_app.cli, ["--help"])
    assert result.exit_code == 0
    for name in ("repl", "version", "doctor", "config", "completion", "debug"):
        assert name in result.output


def test_bare_invocation_routes_to_repl_stub() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_app.cli, [])
    # repl stub raises SystemExit(0); CliRunner surfaces exit code.
    assert result.exit_code == 0


def test_positional_prompt_also_routes_to_repl_stub() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_app.cli, ["write", "a", "haiku"])
    assert result.exit_code == 0


