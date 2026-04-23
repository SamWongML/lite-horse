import json

import pytest

from lite_horse.cli import _output


def _lines(s: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in s.splitlines() if line]


def test_emit_result_ndjson(capsys: pytest.CaptureFixture[str]) -> None:
    _output.emit_result({"k": 1}, json_mode=True)
    out = capsys.readouterr().out
    records = _lines(out)
    assert records == [{"kind": "result", "data": {"k": 1}}]


def test_emit_item_human_prints_string(capsys: pytest.CaptureFixture[str]) -> None:
    _output.emit_item("hello", json_mode=False)
    assert capsys.readouterr().out == "hello\n"


def test_emit_progress_human_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    _output.emit_progress("working", 0.5, json_mode=False)
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "working" in cap.err
    assert "50%" in cap.err


def test_emit_progress_json_goes_to_stdout_only(capsys: pytest.CaptureFixture[str]) -> None:
    _output.emit_progress("step", 0.25, json_mode=True)
    cap = capsys.readouterr()
    assert cap.err == ""
    assert _lines(cap.out) == [{"kind": "progress", "stage": "step", "pct": 0.25}]


def test_emit_error_human_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    _output.emit_error("nope", code=3, json_mode=False)
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "error: nope" in cap.err


def test_emit_error_json_includes_code(capsys: pytest.CaptureFixture[str]) -> None:
    _output.emit_error("nope", code=3, json_mode=True)
    records = _lines(capsys.readouterr().out)
    assert records == [{"kind": "error", "code": 3, "message": "nope"}]
