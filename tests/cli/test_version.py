import json

from typer.testing import CliRunner

from lite_horse.cli.commands.version import app


def test_version_human() -> None:
    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert result.stdout.strip()  # some version string


def test_version_json() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--json"])
    assert result.exit_code == 0
    lines = [line for line in result.stdout.splitlines() if line]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "result"
    assert "version" in record["data"]
