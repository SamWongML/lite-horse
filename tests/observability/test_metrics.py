"""Phase 38 — EMF metric line shape."""
from __future__ import annotations

import io
import json

from lite_horse.observability.metrics import emit_metric


def test_emit_metric_writes_emf_json_line():
    buf = io.StringIO()
    emit_metric(
        "turns_total",
        1,
        dimensions={"model": "gpt-5.4"},
        stream=buf,
    )
    raw = buf.getvalue().strip()
    payload = json.loads(raw)
    aws = payload["_aws"]
    assert isinstance(aws["Timestamp"], int)
    cw = aws["CloudWatchMetrics"][0]
    assert cw["Namespace"] == "litehorse"
    assert cw["Dimensions"] == [["model"]]
    assert cw["Metrics"] == [{"Name": "turns_total", "Unit": "Count"}]
    assert payload["turns_total"] == 1
    assert payload["model"] == "gpt-5.4"


def test_emit_metric_handles_no_dimensions():
    buf = io.StringIO()
    emit_metric("errors_total", 1, stream=buf)
    payload = json.loads(buf.getvalue().strip())
    cw = payload["_aws"]["CloudWatchMetrics"][0]
    assert cw["Dimensions"] == [[]]


def test_emit_metric_supports_unit_and_extra():
    buf = io.StringIO()
    emit_metric(
        "http_request_duration_ms",
        42.5,
        unit="Milliseconds",
        dimensions={"method": "POST", "status_class": "2xx"},
        extra={"path": "/v1/turns"},
        stream=buf,
    )
    payload = json.loads(buf.getvalue().strip())
    cw = payload["_aws"]["CloudWatchMetrics"][0]
    assert cw["Metrics"] == [
        {"Name": "http_request_duration_ms", "Unit": "Milliseconds"}
    ]
    assert payload["http_request_duration_ms"] == 42.5
    assert payload["path"] == "/v1/turns"
