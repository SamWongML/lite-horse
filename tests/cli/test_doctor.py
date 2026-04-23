import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lite_horse.cli.commands.doctor import app
from lite_horse.cli.exit_codes import ExitCode


def test_doctor_reports_degraded_without_api_key(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code == int(ExitCode.CONFIG)
    assert "openai_api_key" in result.stdout


def test_doctor_ok_with_api_key(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-abc")
    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "ok" in result.stdout


def test_doctor_json_emits_ndjson(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-abc")
    runner = CliRunner()
    result = runner.invoke(app, ["--json"])
    assert result.exit_code == 0
    records = [json.loads(line) for line in result.stdout.splitlines() if line]
    # every line parses; at least one is kind=result
    assert all("kind" in r for r in records)
    kinds = {r["kind"] for r in records}
    assert "item" in kinds
    assert "result" in kinds


def test_doctor_lists_configured_mcp_servers(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-abc")
    (litehorse_home / "config.yaml").write_text(
        "model: gpt-5.4\n"
        "mcp_servers:\n"
        "  - name: pm-broker\n"
        "    url: http://localhost:7444/mcp\n"
    )
    runner = CliRunner()
    result = runner.invoke(app, ["--json"])
    assert result.exit_code == 0
    records = [json.loads(line) for line in result.stdout.splitlines() if line]
    mcp = next(r for r in records if r.get("data", {}).get("name") == "mcp_servers")
    assert mcp["data"]["configured"] == ["pm-broker"]
