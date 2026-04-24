"""Tests for the Phase 30 logging bootstrap in ``lite_horse.cli._logging``."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from lite_horse.cli import _logging as cli_logging


@pytest.fixture(autouse=True)
def _reset_root_logger() -> object:
    """Keep each test hermetic — drop all handlers on teardown."""
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for h in list(root.handlers):
        if h not in before:
            root.removeHandler(h)


def test_configure_writes_log_to_file(litehorse_home: Path) -> None:
    cli_logging.configure(json_mode=False, debug=False)
    logging.getLogger("lite_horse.test").info("hello file")
    for h in logging.getLogger().handlers:
        h.flush()

    log_file = litehorse_home / "litehorse.log"
    assert log_file.exists(), "configure() must create the log file"
    body = log_file.read_text(encoding="utf-8")
    assert "hello file" in body


def test_structured_mode_emits_json_to_stderr(
    litehorse_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_logging.configure(json_mode=True, debug=False)
    logging.getLogger("lite_horse.test").warning("structured line")
    for h in logging.getLogger().handlers:
        h.flush()

    captured = capsys.readouterr()
    json_lines = [
        line for line in captured.err.splitlines() if line.startswith("{")
    ]
    assert json_lines, "structured mode must emit JSON on stderr"
    record = json.loads(json_lines[-1])
    assert record["msg"] == "structured line"
    assert record["level"] == "WARNING"
    assert record["name"] == "lite_horse.test"


def test_env_flag_enables_structured_without_json_flag(
    litehorse_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LITEHORSE_STRUCTURED_LOGS", "1")
    cli_logging.configure(json_mode=False, debug=False)
    logging.getLogger("lite_horse.test").info("env-driven")
    for h in logging.getLogger().handlers:
        h.flush()

    captured = capsys.readouterr()
    json_lines = [line for line in captured.err.splitlines() if line.startswith("{")]
    assert json_lines, "LITEHORSE_STRUCTURED_LOGS=1 must activate JSON mode"


def test_configure_is_idempotent(litehorse_home: Path) -> None:
    cli_logging.configure(json_mode=True)
    cli_logging.configure(json_mode=True)
    cli_logging.configure(json_mode=True)

    own = [
        h for h in logging.getLogger().handlers
        if getattr(h, "_litehorse_installed", False)
    ]
    # Exactly one file + one stderr handler after three calls.
    assert len(own) == 2


def test_debug_flag_lowers_root_level(litehorse_home: Path) -> None:
    cli_logging.configure(json_mode=False, debug=True)
    assert logging.getLogger().level == logging.DEBUG


def test_is_structured_gates_on_env_and_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LITEHORSE_STRUCTURED_LOGS", raising=False)
    assert cli_logging.is_structured(json_mode=False) is False
    assert cli_logging.is_structured(json_mode=True) is True
    monkeypatch.setenv("LITEHORSE_STRUCTURED_LOGS", "yes")
    assert cli_logging.is_structured(json_mode=False) is True
