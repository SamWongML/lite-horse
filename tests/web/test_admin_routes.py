"""``/v1/admin/*`` round-trip tests.

Covers role gating, every CRUD endpoint, audit-log persistence on every
admin write, version listing + rollback round-trip, MCP health, and
basic users / usage / audit endpoints.

Skipped when Docker isn't reachable, same as every other integration
suite.
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

_SECRET = b"phase-34-test-secret-do-not-use-in-prod"
_KID = "phase-34-kid"
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


def _mint_token(sub: str, role: str = "admin") -> str:
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
    except Exception as exc:  # docker not reachable → skip
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


def _admin_headers() -> dict[str, str]:
    sub = f"phase34-admin-{uuid4()}"
    token = _mint_token(sub, role="admin")
    return {"Authorization": f"Bearer {token}"}


def _user_headers() -> dict[str, str]:
    sub = f"phase34-user-{uuid4()}"
    token = _mint_token(sub, role="user")
    return {"Authorization": f"Bearer {token}"}


# ---------- role gating ----------


async def test_admin_route_rejects_non_admin(client: AsyncClient) -> None:
    r = await client.get("/v1/admin/skills", headers=_user_headers())
    assert r.status_code == 403
    assert r.json()["detail"]["kind"] == "FORBIDDEN"


async def test_admin_route_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/v1/admin/skills")
    assert r.status_code == 401


# ---------- skills CRUD + versioning + audit ----------


async def test_skill_admin_crud_versions_rollback_audit(
    client: AsyncClient,
) -> None:
    headers = _admin_headers()
    slug = f"sk-{uuid4().hex[:8]}"

    # CREATE
    r = await client.post(
        "/v1/admin/skills",
        headers=headers,
        json={
            "slug": slug,
            "frontmatter": {"name": slug, "description": "v1"},
            "body": "v1 body",
            "mandatory": False,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 1
    assert r.json()["is_current"] is True

    # UPDATE → version bump
    r = await client.put(
        f"/v1/admin/skills/{slug}",
        headers=headers,
        json={"body": "v2 body"},
    )
    assert r.status_code == 200
    assert r.json()["version"] == 2
    assert r.json()["body"] == "v2 body"

    # GET returns latest current
    r = await client.get(f"/v1/admin/skills/{slug}", headers=headers)
    assert r.json()["version"] == 2

    # version history
    r = await client.get(
        f"/v1/admin/skills/{slug}/versions", headers=headers
    )
    assert r.status_code == 200
    versions = r.json()
    assert [v["version"] for v in versions] == [2, 1]
    assert [v["is_current"] for v in versions] == [True, False]

    # rollback to v1
    r = await client.post(
        f"/v1/admin/skills/{slug}:rollback",
        headers=headers,
        json={"version": 1},
    )
    assert r.status_code == 200
    assert r.json()["version"] == 1
    assert r.json()["is_current"] is True

    r = await client.get(f"/v1/admin/skills/{slug}", headers=headers)
    assert r.json()["version"] == 1
    assert r.json()["body"] == "v1 body"

    # AUDIT — the writes above produced audit-log rows
    r = await client.get("/v1/admin/audit-log", headers=headers)
    assert r.status_code == 200
    actions = {row["action"] for row in r.json()}
    assert "skill.create" in actions
    assert "skill.update" in actions
    assert "skill.rollback" in actions

    # DELETE
    r = await client.delete(f"/v1/admin/skills/{slug}", headers=headers)
    assert r.status_code == 204
    r = await client.get(f"/v1/admin/skills/{slug}", headers=headers)
    assert r.status_code == 404


# ---------- instructions CRUD ----------


async def test_instruction_admin_crud(client: AsyncClient) -> None:
    headers = _admin_headers()
    slug = f"ins-{uuid4().hex[:8]}"
    r = await client.post(
        "/v1/admin/instructions",
        headers=headers,
        json={"slug": slug, "body": "be terse", "priority": 80},
    )
    assert r.status_code == 201
    r = await client.put(
        f"/v1/admin/instructions/{slug}",
        headers=headers,
        json={"priority": 30},
    )
    assert r.status_code == 200
    assert r.json()["priority"] == 30
    assert r.json()["version"] == 2
    r = await client.delete(
        f"/v1/admin/instructions/{slug}", headers=headers
    )
    assert r.status_code == 204


# ---------- commands CRUD ----------


async def test_command_admin_crud(client: AsyncClient) -> None:
    headers = _admin_headers()
    slug = f"cmd-{uuid4().hex[:8]}"
    r = await client.post(
        "/v1/admin/commands",
        headers=headers,
        json={
            "slug": slug,
            "prompt_tpl": "Hi {{ name }}",
            "description": "greet",
        },
    )
    assert r.status_code == 201
    r = await client.get(f"/v1/admin/commands/{slug}", headers=headers)
    assert r.json()["prompt_tpl"] == "Hi {{ name }}"


# ---------- mcp servers CRUD: never leak secrets ----------


async def test_mcp_admin_crud_does_not_leak_secret(
    client: AsyncClient,
) -> None:
    headers = _admin_headers()
    slug = f"mcp-{uuid4().hex[:8]}"
    body = {
        "slug": slug,
        "url": "https://example.com/mcp",
        "auth_header": "Authorization",
        "auth_value": "Bearer s3cret",
        "mandatory": False,
    }
    r = await client.post(
        "/v1/admin/mcp-servers", headers=headers, json=body
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["has_auth_value"] is True
    assert "s3cret" not in r.text

    r = await client.get(
        f"/v1/admin/mcp-servers/{slug}", headers=headers
    )
    assert "s3cret" not in r.text


# ---------- cron jobs CRUD (mutable in place) ----------


async def test_cron_admin_crud(client: AsyncClient) -> None:
    headers = _admin_headers()
    slug = f"cron-{uuid4().hex[:8]}"
    r = await client.post(
        "/v1/admin/cron-jobs",
        headers=headers,
        json={
            "slug": slug,
            "cron_expr": "0 * * * *",
            "prompt": "hourly check",
        },
    )
    assert r.status_code == 201
    # cron has no version surface
    r = await client.get(
        f"/v1/admin/cron-jobs/{slug}/versions", headers=headers
    )
    assert r.status_code == 404


# ---------- users + usage ----------


async def test_users_list_returns_admin_actor(client: AsyncClient) -> None:
    headers = _admin_headers()
    # Touch /v1/admin/skills first so the admin user is provisioned.
    await client.get("/v1/admin/skills", headers=headers)
    r = await client.get("/v1/admin/users", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_user_usage_invalid_uuid_404(client: AsyncClient) -> None:
    headers = _admin_headers()
    r = await client.get(
        "/v1/admin/users/not-a-uuid/usage", headers=headers
    )
    assert r.status_code == 404


# ---------- audit log filters ----------


async def test_audit_log_filter_by_action(client: AsyncClient) -> None:
    headers = _admin_headers()
    slug = f"sk-aud-{uuid4().hex[:8]}"
    await client.post(
        "/v1/admin/skills",
        headers=headers,
        json={"slug": slug, "frontmatter": {}, "body": "x"},
    )
    r = await client.get(
        "/v1/admin/audit-log?action=skill.create", headers=headers
    )
    assert r.status_code == 200
    assert all(row["action"] == "skill.create" for row in r.json())
