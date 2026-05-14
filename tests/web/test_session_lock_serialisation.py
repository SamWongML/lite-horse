"""Acceptance — same-`session_key` turns serialise via the wired lock.

This is the contract `web/app.py`'s lifespan must satisfy: two concurrent
``POST /v1/turns`` for the same user + session_key cannot both be inside
the runner at once. Tests inject a deterministic runner that records the
order of enter/exit so we can assert serialisation directly.
"""
from __future__ import annotations

import asyncio
import base64
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

from lite_horse.storage.locks_memory import InMemorySessionLock
from lite_horse.web import auth as auth_mod
from lite_horse.web import create_app
from lite_horse.web.permissions import PermissionBroker
from lite_horse.web.routes.turns import (
    get_redis,
    get_session_lock,
    get_turn_runner,
)
from lite_horse.web.turns import TurnRegistry, TurnRequest

pytestmark = pytest.mark.asyncio

_SECRET = b"session-lock-test-secret-do-not-use-in-prod"
_KID = "session-lock-kid"
_AUDIENCE = "lite-horse"
_ISSUER = "http://localhost:9999"


@dataclass
class _FakeRunResult:
    final_output: str
    input_tokens: int = 1
    output_tokens: int = 1


class StreamDelta:
    def __init__(self, text: str) -> None:
        self.text = text


class StreamDone:
    def __init__(self, result: _FakeRunResult) -> None:
        self.result = result


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


def _mint(sub: str = "session-lock-user") -> str:
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


async def test_same_session_key_serialises_through_lock() -> None:
    """Two concurrent POST /v1/turns must not overlap inside the runner."""
    inside = 0
    overlap_seen = False
    order: list[str] = []

    async def runner(req: TurnRequest) -> AsyncIterator[Any]:
        nonlocal inside, overlap_seen
        inside += 1
        order.append(f"{req.text}:enter")
        if inside > 1:
            overlap_seen = True
        try:
            await asyncio.sleep(0.05)
            yield StreamDelta(text=f"ack:{req.text}")
            yield StreamDone(result=_FakeRunResult(final_output=req.text))
        finally:
            inside -= 1
            order.append(f"{req.text}:exit")

    lock = InMemorySessionLock()
    app = create_app()
    app.dependency_overrides[get_turn_runner] = lambda: runner
    app.dependency_overrides[get_session_lock] = lambda: lock
    app.dependency_overrides[get_redis] = lambda: None
    app.state.turn_registry = TurnRegistry()
    app.state.permission_broker = PermissionBroker(redis=None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        token = _mint()
        headers = {"Authorization": f"Bearer {token}"}
        r1, r2 = await asyncio.gather(
            client.post(
                "/v1/turns",
                headers=headers,
                json={"session_key": "shared", "text": "a"},
            ),
            client.post(
                "/v1/turns",
                headers=headers,
                json={"session_key": "shared", "text": "b"},
            ),
        )
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text

    assert overlap_seen is False, f"runner overlapped: {order}"
    # Each turn entered then exited before the next entered — strict serial.
    assert order in (
        ["a:enter", "a:exit", "b:enter", "b:exit"],
        ["b:enter", "b:exit", "a:enter", "a:exit"],
    ), order


async def test_distinct_session_keys_run_in_parallel() -> None:
    """Lock is per-key; turns on different keys must NOT serialise."""
    inside = 0
    max_concurrent = 0

    async def runner(req: TurnRequest) -> AsyncIterator[Any]:
        nonlocal inside, max_concurrent
        inside += 1
        max_concurrent = max(max_concurrent, inside)
        try:
            await asyncio.sleep(0.05)
            yield StreamDone(result=_FakeRunResult(final_output=req.text))
        finally:
            inside -= 1

    lock = InMemorySessionLock()
    app = create_app()
    app.dependency_overrides[get_turn_runner] = lambda: runner
    app.dependency_overrides[get_session_lock] = lambda: lock
    app.dependency_overrides[get_redis] = lambda: None
    app.state.turn_registry = TurnRegistry()
    app.state.permission_broker = PermissionBroker(redis=None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        token = _mint()
        headers = {"Authorization": f"Bearer {token}"}
        r1, r2 = await asyncio.gather(
            client.post(
                "/v1/turns",
                headers=headers,
                json={"session_key": "alpha", "text": "x"},
            ),
            client.post(
                "/v1/turns",
                headers=headers,
                json={"session_key": "beta", "text": "y"},
            ),
        )
        assert r1.status_code == 200
        assert r2.status_code == 200

    assert max_concurrent == 2, "distinct session keys should run in parallel"
