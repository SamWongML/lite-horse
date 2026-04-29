"""Phase 38 — request-id + logging + metrics middleware behaviour."""
from __future__ import annotations

import io
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lite_horse.observability import (
    LoggingMiddleware,
    MetricsMiddleware,
    RequestIdMiddleware,
    configure_logging,
)
from lite_horse.observability import metrics as metrics_module
from lite_horse.observability.logs import clear_log_context


@pytest.fixture(autouse=True)
def _reset_logging():
    configure_logging(env="prod", force=True)
    yield
    clear_log_context()


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    return app


async def test_request_id_echoed_when_supplied():
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/ping", headers={"X-Request-Id": "rid-test"})
    assert resp.status_code == 200
    assert resp.headers["x-request-id"] == "rid-test"


async def test_request_id_generated_when_missing():
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/ping")
    assert resp.status_code == 200
    assert resp.headers["x-request-id"]


async def test_logging_middleware_writes_access_line(capsys):
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        await client.get("/ping", headers={"X-Request-Id": "rid-log"})
    captured = capsys.readouterr().out
    access = [
        json.loads(line)
        for line in captured.splitlines()
        if line.startswith("{") and '"http_request"' in line
    ]
    assert access, captured
    line = access[0]
    assert line["method"] == "GET"
    assert line["path"] == "/ping"
    assert line["status"] == 200
    assert isinstance(line["latency_ms"], float)
    assert line["request_id"] == "rid-log"


async def test_metrics_middleware_emits_emf(monkeypatch):
    app = _build_app()
    buf = io.StringIO()
    real_emit = metrics_module.emit_metric

    def _capture(name, value, **kw):
        kw.setdefault("stream", buf)
        return real_emit(name, value, **kw)

    monkeypatch.setattr(
        "lite_horse.observability.middleware.emit_metric", _capture
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/ping")
    assert resp.status_code == 200
    lines = [json.loads(x) for x in buf.getvalue().splitlines() if x.startswith("{")]
    names = [
        line["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Name"] for line in lines
    ]
    assert "http_requests_total" in names
    assert "http_request_duration_ms" in names
    count_line = next(line for line in lines if "http_requests_total" in line)
    assert count_line["method"] == "GET"
    assert count_line["status_class"] == "2xx"
