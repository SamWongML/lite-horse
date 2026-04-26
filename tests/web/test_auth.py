"""JWT auth happy-path + error paths + admin gating.

JWKS fetch is stubbed via `monkeypatch` on `auth._get_jwks`; the lazy
user-row creation is stubbed via `auth._lookup_or_create_user` so this
suite does not need Postgres.
"""
from __future__ import annotations

import base64
import time
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

from lite_horse.web import auth as auth_mod
from lite_horse.web import create_app

_SECRET = b"unit-test-secret-do-not-use-in-prod"
_KID = "unit-kid-1"
_AUDIENCE = "lite-horse"
_ISSUER = "http://localhost:9999"


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


def _mint(
    *,
    sub: str | None = "user-abc",
    role: str = "user",
    aud: str = _AUDIENCE,
    iss: str = _ISSUER,
    extra: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "aud": aud,
        "iss": iss,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "role": role,
    }
    if sub is not None:
        payload["sub"] = sub
    if extra:
        payload.update(extra)
    return jwt.encode(payload, _SECRET, algorithm="HS256", headers={"kid": _KID})


@pytest.fixture(autouse=True)
def _stub_jwks_and_user(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_jwks(_url: str) -> dict[str, Any]:
        return _jwks()

    async def fake_lookup(_external_id: str, _role: str) -> str:
        return str(uuid.uuid4())

    monkeypatch.setattr(auth_mod, "_get_jwks", fake_jwks)
    monkeypatch.setattr(auth_mod, "_lookup_or_create_user", fake_lookup)
    auth_mod._clear_jwks_cache()


@pytest.fixture
def app():
    # debug routes are mounted only when env=local — that's the local default.
    return create_app()


async def _get(app, path: str, headers: dict[str, str] | None = None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(path, headers=headers or {})


async def test_whoami_happy_path(app):
    token = _mint(sub="user-abc", role="user")
    resp = await _get(
        app, "/v1/_debug/whoami", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["external_id"] == "user-abc"
    assert body["role"] == "user"
    assert body["request_id"]


async def test_missing_authorization_header_returns_401(app):
    resp = await _get(app, "/v1/_debug/whoami")
    assert resp.status_code == 401
    assert resp.json()["detail"]["kind"] == "UNAUTHORIZED"


async def test_invalid_token_returns_401(app):
    resp = await _get(
        app, "/v1/_debug/whoami", headers={"Authorization": "Bearer not.a.jwt"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["kind"] == "UNAUTHORIZED"


async def test_missing_sub_claim_returns_401(app):
    token = _mint(sub=None)
    resp = await _get(
        app, "/v1/_debug/whoami", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401
    assert "sub" in resp.json()["detail"]["message"]


async def test_admin_gate_rejects_non_admin(app):
    token = _mint(sub="user-abc", role="user")
    resp = await _get(
        app, "/v1/_debug/admin-only", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["kind"] == "FORBIDDEN"


async def test_admin_gate_accepts_admin(app):
    token = _mint(sub="root", role="admin")
    resp = await _get(
        app, "/v1/_debug/admin-only", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


async def test_unknown_kid_returns_401(app, monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty_jwks(_url: str) -> dict[str, Any]:
        return {"keys": []}

    monkeypatch.setattr(auth_mod, "_get_jwks", empty_jwks)
    auth_mod._clear_jwks_cache()
    token = _mint(sub="user-abc")
    resp = await _get(
        app, "/v1/_debug/whoami", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401
    assert "signing key not found" in resp.json()["detail"]["message"]
