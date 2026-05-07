"""``/v1/users/me/agents/*`` HTTP round-trip tests (Phase 41).

Reuses the JWT + Postgres harness from ``test_user_config.py``: each
``_auth_headers()`` call mints a fresh JWT for an isolated user. The
auto-created default agent is asserted on first list, then a second
``coder`` agent is created, promoted to default, archived (rejected on
the still-default), unarchived by promoting back, etc.
"""
from __future__ import annotations

import base64
import os
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from jose import jwt

from lite_horse.storage.kms_local import LocalKms
from lite_horse.web import auth as auth_mod
from lite_horse.web.deps import get_kms, get_redis

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_SECRET = b"phase-41-test-secret-do-not-use-in-prod"
_KID = "phase-41-kid"
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


def _mint_token(sub: str, role: str = "user") -> str:
    return jwt.encode(
        {
            "sub": sub,
            "role": role,
            "aud": _AUDIENCE,
            "iss": _ISSUER,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        },
        _SECRET,
        algorithm="HS256",
        headers={"kid": _KID},
    )


@pytest.fixture(scope="module")
def _pg_url() -> Iterator[str]:
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")
    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:
        pytest.skip(f"Docker unavailable: {exc!r}")
    raw = container.get_connection_url()
    async_url = raw.replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")
    try:
        yield async_url
    finally:
        container.stop()


@pytest.fixture(scope="module")
def _migrated_pg(_pg_url: str) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["LITEHORSE_DATABASE_URL"] = _pg_url
    subprocess.run(
        [
            "uv",
            "run",
            "alembic",
            "-c",
            "src/lite_horse/alembic.ini",
            "upgrade",
            "head",
        ],
        check=True,
        cwd=repo_root,
        env=env,
    )
    return _pg_url


_TEST_FERNET_KEY = Fernet.generate_key()


def _make_kms() -> LocalKms:
    return LocalKms(_TEST_FERNET_KEY)


@pytest_asyncio.fixture()
async def client(
    _migrated_pg: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("LITEHORSE_DATABASE_URL", _migrated_pg)
    monkeypatch.setenv("LITEHORSE_ENV", "local")

    from lite_horse.config import get_settings
    from lite_horse.storage import db as db_mod

    get_settings.cache_clear()
    db_mod.reset_engine_for_tests()

    async def fake_jwks(_url: str) -> dict[str, Any]:
        return _jwks()

    monkeypatch.setattr(auth_mod, "_get_jwks", fake_jwks)
    auth_mod._clear_jwks_cache()

    from lite_horse.web import create_app

    app = create_app()
    app.dependency_overrides[get_kms] = _make_kms
    app.dependency_overrides[get_redis] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        try:
            yield ac
        finally:
            await db_mod.dispose_engine()


def _auth_headers() -> dict[str, str]:
    sub = f"phase41-{uuid4()}"
    token = _mint_token(sub)
    return {"Authorization": f"Bearer {token}"}


async def test_lists_default_agent_on_first_sight(client: AsyncClient) -> None:
    headers = _auth_headers()
    r = await client.get("/v1/users/me/agents", headers=headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["slug"] == "default"
    assert rows[0]["is_default"] is True
    assert rows[0]["permission_mode"] == "auto"


async def test_create_then_list(client: AsyncClient) -> None:
    headers = _auth_headers()
    # ensure default exists first
    await client.get("/v1/users/me/agents", headers=headers)
    r = await client.post(
        "/v1/users/me/agents",
        headers=headers,
        json={
            "slug": "coder",
            "name": "Coder",
            "persona": "Acts like a senior engineer.",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "coder"
    assert body["is_default"] is False

    r = await client.get("/v1/users/me/agents", headers=headers)
    rows = r.json()
    slugs = {a["slug"] for a in rows}
    assert slugs == {"default", "coder"}


async def test_duplicate_slug_conflicts(client: AsyncClient) -> None:
    headers = _auth_headers()
    payload = {"slug": "shopper", "name": "Shopper"}
    r = await client.post(
        "/v1/users/me/agents", headers=headers, json=payload
    )
    assert r.status_code == 201
    r = await client.post(
        "/v1/users/me/agents", headers=headers, json=payload
    )
    assert r.status_code == 409


async def test_set_default_promotes_and_demotes(client: AsyncClient) -> None:
    headers = _auth_headers()
    create = await client.post(
        "/v1/users/me/agents",
        headers=headers,
        json={"slug": "writer", "name": "Writer"},
    )
    assert create.status_code == 201
    new_id = create.json()["id"]

    promote = await client.post(
        f"/v1/users/me/agents/{new_id}:default", headers=headers
    )
    assert promote.status_code == 200
    assert promote.json()["is_default"] is True

    rows = (await client.get("/v1/users/me/agents", headers=headers)).json()
    defaults = [a for a in rows if a["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["slug"] == "writer"


async def test_cannot_archive_default(client: AsyncClient) -> None:
    headers = _auth_headers()
    rows = (await client.get("/v1/users/me/agents", headers=headers)).json()
    default_id = next(a["id"] for a in rows if a["is_default"])
    r = await client.delete(
        f"/v1/users/me/agents/{default_id}", headers=headers
    )
    assert r.status_code == 409


async def test_archive_non_default_then_excluded_from_list(
    client: AsyncClient,
) -> None:
    headers = _auth_headers()
    create = await client.post(
        "/v1/users/me/agents",
        headers=headers,
        json={"slug": "scratch", "name": "Scratch"},
    )
    assert create.status_code == 201
    aid = create.json()["id"]
    r = await client.delete(f"/v1/users/me/agents/{aid}", headers=headers)
    assert r.status_code == 204
    rows = (await client.get("/v1/users/me/agents", headers=headers)).json()
    assert all(a["id"] != aid for a in rows)


async def test_update_persona_and_permission_mode(client: AsyncClient) -> None:
    headers = _auth_headers()
    rows = (await client.get("/v1/users/me/agents", headers=headers)).json()
    default_id = next(a["id"] for a in rows if a["is_default"])
    r = await client.put(
        f"/v1/users/me/agents/{default_id}",
        headers=headers,
        json={
            "persona": "Speaks in haiku.",
            "permission_mode": "ask",
            "rate_limit_per_min": 5,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["persona"] == "Speaks in haiku."
    assert body["permission_mode"] == "ask"
    assert body["rate_limit_per_min"] == 5
