"""Mandatory-official enforcement.

* ``POST /v1/users/me/opt-outs`` returns 422 for a mandatory official.
* A user-scope row that shadows a mandatory official slug is silently
  ignored by the resolver; the resolved entity stays the official one.
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

_SECRET = b"phase-34-mand-secret-do-not-use-in-prod"
_KID = "phase-34-mand-kid"
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


def _mint(sub: str, role: str) -> str:
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


def make_test_kms() -> LocalKms:
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
    app.dependency_overrides[get_kms] = make_test_kms
    app.dependency_overrides[get_redis] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        try:
            yield ac
        finally:
            await db_mod.dispose_engine()


def _admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {_mint(f'a-{uuid4()}', 'admin')}"}


def _user() -> dict[str, str]:
    return {"Authorization": f"Bearer {_mint(f'u-{uuid4()}', 'user')}"}


async def test_opt_out_of_mandatory_official_rejected_422(
    client: AsyncClient,
) -> None:
    admin = _admin()
    slug = f"mand-{uuid4().hex[:8]}"
    r = await client.post(
        "/v1/admin/skills",
        headers=admin,
        json={
            "slug": slug,
            "frontmatter": {"name": slug, "description": "must"},
            "body": "x",
            "mandatory": True,
        },
    )
    assert r.status_code == 201

    user = _user()
    r = await client.post(
        "/v1/users/me/opt-outs",
        headers=user,
        json={"entity": "skill", "slug": slug},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["kind"] == "UNPROCESSABLE"


async def test_opt_out_of_non_mandatory_official_allowed(
    client: AsyncClient,
) -> None:
    admin = _admin()
    slug = f"opt-{uuid4().hex[:8]}"
    r = await client.post(
        "/v1/admin/skills",
        headers=admin,
        json={
            "slug": slug,
            "frontmatter": {"name": slug, "description": "ok"},
            "body": "x",
            "mandatory": False,
        },
    )
    assert r.status_code == 201

    user = _user()
    r = await client.post(
        "/v1/users/me/opt-outs",
        headers=user,
        json={"entity": "skill", "slug": slug},
    )
    assert r.status_code == 201


async def test_user_shadow_of_mandatory_official_silently_ignored(
    client: AsyncClient,
) -> None:
    admin = _admin()
    slug = f"shadow-{uuid4().hex[:8]}"
    r = await client.post(
        "/v1/admin/skills",
        headers=admin,
        json={
            "slug": slug,
            "frontmatter": {"name": slug, "description": "official"},
            "body": "official body",
            "mandatory": True,
        },
    )
    assert r.status_code == 201

    user = _user()
    # User attempts a same-slug shadow.
    r = await client.post(
        "/v1/users/me/skills",
        headers=user,
        json={
            "slug": slug,
            "frontmatter": {"name": slug, "description": "user shadow"},
            "body": "user body",
        },
    )
    assert r.status_code == 201

    # Resolver: the shadow is silently dropped; the official wins.
    r = await client.get("/v1/users/me/effective-config", headers=user)
    assert r.status_code == 200
    skills = r.json()["skills"]
    matching = [s for s in skills if s["slug"] == slug]
    assert len(matching) == 1
    assert matching[0]["scope"] == "official"
    assert matching[0]["mandatory"] is True
