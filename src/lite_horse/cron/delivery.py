"""Webhook delivery for scheduled cron jobs.

Phase 17 routes cron output into the embedding webapp. The body is a signed
JSON POST — ``{"text": ..., "session_key": ...}`` with an HMAC-SHA256 of the
raw body under ``LITEHORSE_WEBHOOK_SECRET`` in the ``X-LiteHorse-Signature``
header. Transient failures (5xx, connection errors) retry with exponential
backoff; 4xx aborts immediately because replay won't help.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-LiteHorse-Signature"
_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 4.0, 16.0)
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


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
    secret = os.environ.get("LITEHORSE_WEBHOOK_SECRET", "")
    if not secret:
        log.error("LITEHORSE_WEBHOOK_SECRET is empty; refusing unsigned webhook POST")
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
