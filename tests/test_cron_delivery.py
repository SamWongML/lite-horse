"""Tests for webhook cron delivery (Phase 17)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import httpx
import pytest

from lite_horse.cron import delivery as delivery_mod
from lite_horse.cron.delivery import SIGNATURE_HEADER, _sign, deliver_webhook


def _verify(secret: str, body: bytes, header_value: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header_value)


@pytest.fixture()
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip backoff delays so retry tests run instantly."""

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(delivery_mod.asyncio, "sleep", _instant)


@pytest.fixture()
def secret(monkeypatch: pytest.MonkeyPatch) -> str:
    s = "super-secret-key"
    monkeypatch.setenv("LITEHORSE_WEBHOOK_SECRET", s)
    return s


def _mock_client(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport
) -> None:
    """Force every ``httpx.AsyncClient`` to route through ``transport``."""
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(delivery_mod.httpx, "AsyncClient", factory)


async def test_happy_path_posts_signed_body(
    monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = bytes(request.content)
        captured["sig"] = request.headers.get(SIGNATURE_HEADER)
        captured["ct"] = request.headers.get("Content-Type")
        return httpx.Response(200, json={"ok": True})

    _mock_client(monkeypatch, httpx.MockTransport(handler))

    await deliver_webhook(
        {"platform": "webhook", "url": "https://example.test/hook"},
        "hello world",
        "sess-abc",
    )

    assert captured["url"] == "https://example.test/hook"
    assert captured["ct"] == "application/json"
    assert json.loads(captured["body"]) == {
        "text": "hello world",
        "session_key": "sess-abc",
    }
    assert _verify(secret, captured["body"], captured["sig"])


async def test_retries_on_503_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    del no_sleep, secret
    calls: list[int] = []

    def handler(_req: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200)

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    caplog.set_level(logging.WARNING, logger="lite_horse.cron.delivery")

    await deliver_webhook(
        {"platform": "webhook", "url": "https://example.test/hook"},
        "t",
        "sess",
    )

    assert len(calls) == 3
    assert any("503" in r.message for r in caplog.records)


async def test_aborts_on_400(
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    del no_sleep, secret
    calls: list[int] = []

    def handler(_req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="bad shape")

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    caplog.set_level(logging.ERROR, logger="lite_horse.cron.delivery")

    await deliver_webhook(
        {"platform": "webhook", "url": "https://example.test/hook"},
        "t",
        "sess",
    )

    assert len(calls) == 1  # no retries on 4xx
    assert any("rejected" in r.message for r in caplog.records)


async def test_retries_on_transport_error_then_gives_up(
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    del no_sleep, secret
    calls: list[int] = []

    def handler(_req: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("refused")

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    caplog.set_level(logging.WARNING, logger="lite_horse.cron.delivery")

    await deliver_webhook(
        {"platform": "webhook", "url": "https://example.test/hook"},
        "t",
        "sess",
    )

    assert len(calls) == 4  # 1 initial + 3 retries
    assert any("exhausted retries" in r.message for r in caplog.records)


async def test_missing_secret_refuses_to_post(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("LITEHORSE_WEBHOOK_SECRET", raising=False)
    calls: list[int] = []

    def handler(_req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200)

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    caplog.set_level(logging.ERROR, logger="lite_horse.cron.delivery")

    await deliver_webhook(
        {"platform": "webhook", "url": "https://example.test/hook"},
        "t",
        "sess",
    )

    assert calls == []
    assert any("LITEHORSE_WEBHOOK_SECRET" in r.message for r in caplog.records)


async def test_missing_url_logs_and_returns(
    secret: str, caplog: pytest.LogCaptureFixture
) -> None:
    del secret
    caplog.set_level(logging.ERROR, logger="lite_horse.cron.delivery")
    await deliver_webhook({"platform": "webhook"}, "t", "sess")
    assert any("missing 'url'" in r.message for r in caplog.records)


def test_sign_produces_stable_hex() -> None:
    sig = _sign("k", b"hello")
    assert sig.startswith("sha256=")
    # Recomputing matches.
    assert sig == _sign("k", b"hello")
    # Different key ⇒ different sig.
    assert sig != _sign("k2", b"hello")
