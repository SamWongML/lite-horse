"""Phase 38 — assert structured log shape.

We force-reconfigure ``structlog`` for each test so the JSON renderer
captures everything ``configure_logging(env="prod")`` produces, then
inspect stdout via ``capsys``.
"""
from __future__ import annotations

import json
import logging

import pytest

from lite_horse.observability.logs import (
    bind_log_context,
    clear_log_context,
    configure_logging,
    get_logger,
)


@pytest.fixture(autouse=True)
def _reset_logging() -> None:
    configure_logging(env="prod", force=True)
    yield
    clear_log_context()


def _read_lines(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    captured = capsys.readouterr()
    out: list[dict] = []
    for raw in captured.out.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        out.append(json.loads(line))
    return out


def test_log_emits_json_with_required_fields(capsys):
    log = get_logger("lite_horse.test")
    log.info("hello", request_id="req-1", user_id="u-1", latency_ms=12.5)
    lines = _read_lines(capsys)
    assert len(lines) == 1
    line = lines[0]
    assert line["event"] == "hello"
    assert line["level"] == "info"
    assert line["request_id"] == "req-1"
    assert line["user_id"] == "u-1"
    assert line["latency_ms"] == 12.5
    assert "timestamp" in line


def test_contextvars_merge_into_log(capsys):
    bind_log_context(request_id="req-2", user_id="u-2", session_key="s-2")
    log = get_logger("lite_horse.test")
    log.info("event", turn_id="turn-1")
    lines = _read_lines(capsys)
    assert lines[0]["request_id"] == "req-2"
    assert lines[0]["user_id"] == "u-2"
    assert lines[0]["session_key"] == "s-2"
    assert lines[0]["turn_id"] == "turn-1"


def test_stdlib_logger_routes_through_structlog(capsys):
    import io

    import structlog

    buf = io.StringIO()
    # Re-point both renderers at an explicit buffer for this assertion.
    handler = logging.StreamHandler(buf)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
            foreign_pre_chain=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
            ],
        )
    )
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        logging.getLogger("lite_horse.adapter").info("plain")
    finally:
        root.removeHandler(handler)
    parsed = json.loads(buf.getvalue().strip().splitlines()[0])
    assert parsed["event"] == "plain"
    assert parsed["level"] == "info"
