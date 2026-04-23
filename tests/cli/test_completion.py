from typer.testing import CliRunner

from lite_horse.cli.commands.completion import app
from lite_horse.cli.exit_codes import ExitCode


def test_install_bash_emits_completion_script() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["install", "bash"])
    assert result.exit_code == 0
    # Click's bash completion contains these markers reliably across versions.
    assert "_litehorse_completion" in result.stdout
    assert "complete" in result.stdout


def test_install_zsh() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["install", "zsh"])
    assert result.exit_code == 0
    assert "litehorse" in result.stdout


def test_install_fish() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["install", "fish"])
    assert result.exit_code == 0
    assert "litehorse" in result.stdout


def test_install_unknown_shell_is_usage_error() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["install", "tcsh"])
    assert result.exit_code == int(ExitCode.USAGE)
