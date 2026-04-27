"""Redis-backed idempotency cache for ``POST /v1/turns*``.

A retried request that carries the same ``Idempotency-Key`` header for
the same ``user_id`` returns the cached response **without re-invoking
the agent**. The cache is intentionally narrow:

* Key shape: ``idem:{user_id}:{idempotency_key}``
* TTL: 24 h (matches the spec).
* Stored payload is the JSON response body for non-streaming turns, or
  the raw concatenated SSE bytes (base64-encoded) for streaming turns.

The cache stores **completed** responses only — partial work in flight
is not surfaced via Idempotency-Key (it serialises through the per-key
distributed lock instead).
"""
from __future__ import annotations

import base64
import json
from typing import Any

from lite_horse.storage.redis_client import Redis

IDEMPOTENCY_TTL_SECONDS = 24 * 3600

_PREFIX = "idem:"


def _key(user_id: str, idem_key: str) -> str:
    return f"{_PREFIX}{user_id}:{idem_key}"


async def get_cached_json(
    redis: Redis | None, *, user_id: str, idem_key: str
) -> dict[str, Any] | None:
    if redis is None or not idem_key:
        return None
    raw = await redis.get(_key(user_id, idem_key))
    if raw is None:
        return None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(body, dict) and body.get("kind") == "json":
        cached: dict[str, Any] = body.get("body", {})
        return cached
    return None


async def store_cached_json(
    redis: Redis | None,
    *,
    user_id: str,
    idem_key: str,
    body: dict[str, Any],
) -> None:
    if redis is None or not idem_key:
        return
    payload = json.dumps({"kind": "json", "body": body})
    await redis.setex(_key(user_id, idem_key), IDEMPOTENCY_TTL_SECONDS, payload)


async def get_cached_stream(
    redis: Redis | None, *, user_id: str, idem_key: str
) -> bytes | None:
    if redis is None or not idem_key:
        return None
    raw = await redis.get(_key(user_id, idem_key))
    if raw is None:
        return None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(body, dict) and body.get("kind") == "sse":
        encoded = body.get("body", "")
        try:
            return base64.b64decode(encoded.encode("ascii"))
        except (ValueError, AttributeError):
            return None
    return None


async def store_cached_stream(
    redis: Redis | None,
    *,
    user_id: str,
    idem_key: str,
    sse_bytes: bytes,
) -> None:
    if redis is None or not idem_key:
        return
    encoded = base64.b64encode(sse_bytes).decode("ascii")
    payload = json.dumps({"kind": "sse", "body": encoded})
    await redis.setex(_key(user_id, idem_key), IDEMPOTENCY_TTL_SECONDS, payload)
