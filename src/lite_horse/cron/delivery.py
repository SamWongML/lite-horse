"""Webhook delivery for scheduled cron jobs.

Phase 17 routes cron output into the embedding webapp. The body is a signed
JSON POST — ``{"text": ..., "session_key": ...}`` with an HMAC-SHA256 of the
raw body under the webhook secret in the ``X-LiteHorse-Signature`` header.
Transient failures (5xx, connection errors) retry with exponential
backoff; 4xx aborts immediately because replay won't help.

Phase 36: in cloud envs (``LITEHORSE_ENV != local``) the HMAC key
resolves through :class:`SecretsProvider` against
``Settings.webhook_secret_name``. Local dev keeps reading
``LITEHORSE_WEBHOOK_SECRET`` direct from env so unit tests don't need a
secrets backend.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-LiteHorse-Signature"
_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 4.0, 16.0)
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_SECRET_TTL_SECONDS = 300.0
_secret_cache: dict[str, tuple[float, str]] = {}


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def _resolve_secret() -> str:
    """Return the webhook HMAC key. Empty string if unset.

    Local env: env var (no SecretsProvider round trip — keeps tests
    offline). Anything else: SecretsProvider with a small TTL cache so
    each delivery doesn't hit Secrets Manager.
    """
    # Local fast-path — no SecretsProvider, no caching.
    from lite_horse.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    if settings.env == "local":
        return os.environ.get("LITEHORSE_WEBHOOK_SECRET", "")

    name = settings.webhook_secret_name
    now = time.monotonic()
    cached = _secret_cache.get(name)
    if cached is not None and now - cached[0] < _SECRET_TTL_SECONDS:
        return cached[1]
    from lite_horse.storage import make_secrets_provider  # noqa: PLC0415

    provider = make_secrets_provider()
    try:
        value = await provider.get(name)
    except Exception:
        log.exception("webhook secret lookup failed for %s", name)
        return ""
    _secret_cache[name] = (now, value)
    return value


def _clear_secret_cache_for_tests() -> None:
    _secret_cache.clear()


async def deliver_webhook(
    spec: dict[str, Any], text: str, session_key: str
) -> None:
    """POST the cron output to ``spec['url']`` with an HMAC signature.

    Retries up to three times on 5xx or transport errors with a 1s/4s/16s
    backoff. 4xx responses abort — they indicate the webapp rejected the
    delivery shape, not a transient failure, so replay is pointless.
    """
    url = spec.get("url")
    if not isinstance(url, str) or not url:
        log.error("webhook delivery missing 'url' in spec")
        return
    secret = await _resolve_secret()
    if not secret:
        log.error(
            "webhook secret is empty (LITEHORSE_WEBHOOK_SECRET / "
            "Secrets Manager unset); refusing unsigned webhook POST"
        )
        return

    body = json.dumps(
        {"text": text, "session_key": session_key}, ensure_ascii=False
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: _sign(secret, body),
    }

    max_attempts = len(_BACKOFF_SECONDS) + 1
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(max_attempts):
            if attempt > 0:
                await asyncio.sleep(_BACKOFF_SECONDS[attempt - 1])
            try:
                resp = await client.post(url, content=body, headers=headers)
            except httpx.TransportError as exc:
                log.warning(
                    "webhook attempt %d/%d transport error: %s",
                    attempt + 1,
                    max_attempts,
                    exc,
                )
                continue
            if 500 <= resp.status_code < 600:
                log.warning(
                    "webhook attempt %d/%d got %d from %s",
                    attempt + 1,
                    max_attempts,
                    resp.status_code,
                    url,
                )
                continue
            if 400 <= resp.status_code < 500:
                log.error(
                    "webhook delivery to %s rejected: %d %s",
                    url,
                    resp.status_code,
                    resp.text[:200],
                )
                return
            log.info("webhook delivered to %s (%d)", url, resp.status_code)
            return
    log.error("webhook delivery to %s exhausted retries", url)
