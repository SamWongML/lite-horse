"""Tests for `litehorse logs {tail, path}`."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from lite_horse.cli.commands.logs import app


def test_path_prints_log_path(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["path"])
    assert result.exit_code == 0
    assert str(litehorse_home / "litehorse.log") in result.stdout


def test_path_json(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["path", "--json"])
    assert result.exit_code == 0
    record = json.loads(result.stdout.splitlines()[0])
    assert record["data"]["path"].endswith("litehorse.log")


def test_tail_empty_when_no_log_file(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["tail", "-n", "5"])
    assert result.exit_code == 0
    # Only the trailing result line should appear, no items.
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines == [str(litehorse_home / "litehorse.log")]


def test_tail_returns_last_n_lines(litehorse_home: Path) -> None:
    (litehorse_home / "litehorse.log").write_text(
        "\n".join(f"line{i}" for i in range(10)) + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(app, ["tail", "-n", "3"])
    assert result.exit_code == 0
    assert "line7" in result.stdout
    assert "line8" in result.stdout
    assert "line9" in result.stdout
    assert "line0" not in result.stdout
