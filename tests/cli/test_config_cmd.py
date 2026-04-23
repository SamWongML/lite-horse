import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lite_horse.cli.commands.config import app
from lite_horse.cli.exit_codes import ExitCode


def test_path_prints_config_yaml(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["path"])
    assert result.exit_code == 0
    assert str(litehorse_home / "config.yaml") in result.stdout


def test_path_json(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["path", "--json"])
    assert result.exit_code == 0
    record = json.loads(result.stdout.splitlines()[0])
    assert record["kind"] == "result"
    assert record["data"]["path"].endswith("config.yaml")


def test_show_materializes_defaults_on_first_run(litehorse_home: Path) -> None:
    assert not (litehorse_home / "config.yaml").exists()
    runner = CliRunner()
    result = runner.invoke(app, ["show"])
    assert result.exit_code == 0
    assert (litehorse_home / "config.yaml").exists()
    assert "model:" in result.stdout


def test_show_json_returns_parsed_config(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["show", "--json"])
    assert result.exit_code == 0
    record = json.loads(result.stdout.splitlines()[0])
    assert record["kind"] == "result"
    assert "model" in record["data"]


def test_edit_launches_editor_command(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDITOR", "true")  # /usr/bin/true exits 0 without editing
    monkeypatch.delenv("VISUAL", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["edit"])
    assert result.exit_code == 0


def test_edit_missing_editor_returns_io_error(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDITOR", "/nonexistent/editor/binary")
    monkeypatch.delenv("VISUAL", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["edit"])
    assert result.exit_code == int(ExitCode.IO)
