"""``Idempotency-Key`` replay for ``POST /v1/turns*``.

A retried request with the same ``Idempotency-Key`` for the same user
must return the cached response without invoking the runner again. We
verify both invariants: the second response matches the first byte-for-
byte (or shape-for-shape) AND the runner is only invoked once.
"""
from __future__ import annotations

import base64
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

from lite_horse.web import auth as auth_mod
from lite_horse.web import create_app
from lite_horse.web.permissions import PermissionBroker
from lite_horse.web.routes.turns import (
    get_session_lock,
    get_turn_runner,
)
from lite_horse.web.turns import TurnRegistry, TurnRequest

pytestmark = pytest.mark.asyncio

_SECRET = b"phase35-idem-test-secret-do-not-use-in-prod"
_KID = "phase35-idem-kid"
_AUDIENCE = "lite-horse"
_ISSUER = "http://localhost:9999"


@dataclass
class _FakeRunResult:
    final_output: str
    input_tokens: int = 5
    output_tokens: int = 3


class StreamDelta:
    def __init__(self, text: str) -> None:
        self.text = text


class StreamDone:
    def __init__(self, result: _FakeRunResult) -> None:
        self.result = result


class _FakeRedis:
    """In-memory stand-in implementing the slice of redis.asyncio used here."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[float | None, str]] = {}

    async def get(self, key: str) -> str | None:
        item = self.store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at is not None and expires_at <= time.time():
            self.store.pop(key, None)
            return None
        return value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = (time.time() + ttl, value)


def _jwks() -> dict[str, Any]:
    return {
        "keys": [
            {
                "kty": "oct",
                "kid": _KID,
                "alg": "HS256",
                "k": base64.urlsafe_b64encode(_SECRET).rstrip(b"=").decode("ascii"),
            }
        ]
    }


def _mint(sub: str) -> str:
    return jwt.encode(
        {
            "sub": sub,
            "role": "user",
            "aud": _AUDIENCE,
            "iss": _ISSUER,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        },
        _SECRET,
        algorithm="HS256",
        headers={"kid": _KID},
    )


@pytest.fixture(autouse=True)
def _stub_jwks_and_user(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_jwks(_url: str) -> dict[str, Any]:
        return _jwks()

    fixed_uid = str(uuid.uuid4())

    async def fake_lookup(_external_id: str, _role: str) -> str:
        return fixed_uid

    monkeypatch.setattr(auth_mod, "_get_jwks", fake_jwks)
    monkeypatch.setattr(auth_mod, "_lookup_or_create_user", fake_lookup)
    auth_mod._clear_jwks_cache()


def _build_app_with_redis(*, runner, redis: _FakeRedis) -> Any:
    from lite_horse.web.deps import get_redis

    app = create_app()
    app.dependency_overrides[get_turn_runner] = lambda: runner
    app.dependency_overrides[get_session_lock] = lambda: None
    app.dependency_overrides[get_redis] = lambda: redis
    app.state.turn_registry = TurnRegistry()
    app.state.permission_broker = PermissionBroker(redis=None)
    return app


async def test_idempotency_replays_json_response_without_running_again() -> None:
    invocations = 0

    async def runner(_req: TurnRequest) -> AsyncIterator[Any]:
        nonlocal invocations
        invocations += 1
        yield StreamDelta(text="once")
        yield StreamDone(result=_FakeRunResult(final_output="once"))

    redis = _FakeRedis()
    app = _build_app_with_redis(runner=runner, redis=redis)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        token = _mint("idem-user")
        headers = {
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "abc123",
        }
        body = {"session_key": "sess-idem", "text": "do it"}

        first = await client.post("/v1/turns", headers=headers, json=body)
        assert first.status_code == 200
        first_body = first.json()
        assert invocations == 1

        # Replay: same key, agent must not run again.
        second = await client.post("/v1/turns", headers=headers, json=body)
        assert second.status_code == 200
        assert second.json() == first_body
        assert invocations == 1


async def test_idempotency_replays_sse_bytes_byte_for_byte() -> None:
    invocations = 0

    async def runner(_req: TurnRequest) -> AsyncIterator[Any]:
        nonlocal invocations
        invocations += 1
        yield StreamDelta(text="hello")
        yield StreamDone(result=_FakeRunResult(final_output="hello"))

    redis = _FakeRedis()
    app = _build_app_with_redis(runner=runner, redis=redis)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        token = _mint("idem-stream")
        headers = {
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "stream-key",
        }
        body = {"session_key": "sess-idem-stream", "text": "stream"}

        first = await client.post("/v1/turns:stream", headers=headers, json=body)
        assert first.status_code == 200
        first_bytes = first.content
        assert b"event: delta" in first_bytes
        assert invocations == 1

        # Replay: bytes match, runner not invoked again.
        second = await client.post("/v1/turns:stream", headers=headers, json=body)
        assert second.status_code == 200
        assert second.content == first_bytes
        assert invocations == 1


async def test_distinct_keys_run_independently() -> None:
    invocations = 0

    async def runner(_req: TurnRequest) -> AsyncIterator[Any]:
        nonlocal invocations
        invocations += 1
        yield StreamDone(result=_FakeRunResult(final_output=f"call-{invocations}"))

    redis = _FakeRedis()
    app = _build_app_with_redis(runner=runner, redis=redis)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        token = _mint("idem-distinct")
        body = {"session_key": "sess", "text": "hi"}

        for key in ("k1", "k2"):
            resp = await client.post(
                "/v1/turns",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": key,
                },
                json=body,
            )
            assert resp.status_code == 200
        assert invocations == 2


async def test_no_key_means_no_caching() -> None:
    invocations = 0

    async def runner(_req: TurnRequest) -> AsyncIterator[Any]:
        nonlocal invocations
        invocations += 1
        yield StreamDone(result=_FakeRunResult(final_output="x"))

    redis = _FakeRedis()
    app = _build_app_with_redis(runner=runner, redis=redis)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        token = _mint("idem-no-key")
        body = {"session_key": "sess", "text": "hi"}
        for _ in range(2):
            resp = await client.post(
                "/v1/turns",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            assert resp.status_code == 200
        assert invocations == 2
