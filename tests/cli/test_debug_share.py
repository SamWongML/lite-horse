"""Tests for `litehorse debug share`."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lite_horse.cli.commands.debug import app, build_bundle, redact_env


def test_redact_env_scrubs_secrets() -> None:
    out = redact_env({
        "OPENAI_API_KEY": "sk-xxx",
        "LITEHORSE_WEBHOOK_SECRET": "hunter2",
        "SOME_TOKEN": "abc",
        "MY_PASSWORD": "p",
        "LITEHORSE_HOME": "/tmp",
    })
    assert out["OPENAI_API_KEY"] == "<redacted>"
    assert out["LITEHORSE_WEBHOOK_SECRET"] == "<redacted>"
    assert out["SOME_TOKEN"] == "<redacted>"
    assert out["MY_PASSWORD"] == "<redacted>"
    assert out["LITEHORSE_HOME"] == "/tmp"


def test_build_bundle_contains_core_sections(litehorse_home: Path) -> None:
    (litehorse_home / "litehorse.log").write_text(
        "2026-04-25 12:00:00 INFO lite_horse: boot\n"
        "2026-04-25 12:00:01 ERROR lite_horse: kaboom\n",
        encoding="utf-8",
    )
    body = build_bundle(session_key=None, log_lines=50)
    for section in (
        "=== litehorse debug bundle ===",
        "--- env (LITEHORSE_*) ---",
        "--- config.yaml ---",
        "--- session transcript ---",
        "--- log tail",
    ):
        assert section in body
    assert "kaboom" in body


def test_build_bundle_redacts_config_secrets(
    litehorse_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (litehorse_home / "config.yaml").write_text(
        "model: gpt-5.4\nopenai_api_key: sk-super-secret\nlitehorse_token: t\n",
        encoding="utf-8",
    )
    body = build_bundle(session_key=None, log_lines=10)
    assert "sk-super-secret" not in body
    assert "openai_api_key: <redacted>" in body
    assert "litehorse_token: <redacted>" in body


def test_share_writes_temp_file_and_prints_path(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["share", "-n", "10"])
    assert result.exit_code == 0, result.output
    path = result.stdout.strip().splitlines()[-1]
    assert re.search(r"litehorse-share-.*\.txt$", path), path
    contents = Path(path).read_text(encoding="utf-8")
    assert "litehorse debug bundle" in contents


def test_share_json_emits_ndjson(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["share", "--json"])
    assert result.exit_code == 0
    record = json.loads(result.stdout.strip().splitlines()[-1])
    assert record["kind"] == "result"
    assert record["data"]["path"].endswith(".txt")


def test_share_upload_is_not_wired(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["share", "--upload", "paste.rs"])
    assert result.exit_code == 2
    # CliRunner in Click 8.2 merges streams; error hits stderr so it lands
    # in `result.output` either way.
    assert "not implemented" in result.output
