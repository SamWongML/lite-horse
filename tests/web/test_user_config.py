"""``/v1/users/me/*`` HTTP round-trip tests.

Uses a real Postgres container (via the project-wide testcontainer pattern)
plus an in-process ASGI client. JWT verification is monkeypatched to a
local HS256 key; each test mints a JWT for a fresh ``sub`` so the user
row is created lazily and tests don't see each other's state.

Skipped when Docker isn't reachable, same as every other integration suite.
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

_SECRET = b"phase-33c-test-secret-do-not-use-in-prod"
_KID = "phase-33c-kid"
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


# Module-scoped Fernet key so the KMS we hand to the route layer is also
# reachable from individual tests that want to decrypt ciphertext at rest.
_TEST_FERNET_KEY = Fernet.generate_key()


def make_test_kms() -> LocalKms:
    return LocalKms(_TEST_FERNET_KEY)


@pytest_asyncio.fixture()
async def client(
    _migrated_pg: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """ASGI client with JWT, KMS, and Redis stubs in place."""
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


def _auth_headers() -> dict[str, str]:
    """Mint a fresh-user JWT and return Authorization headers.

    Each call uses a brand-new ``sub`` so the lazy ``users`` row insert
    yields an isolated tenant. Tests should call this once and reuse the
    headers for every request in that test so they hit the same user.
    """
    sub = f"phase33c-{uuid4()}"
    token = _mint_token(sub)
    return {"Authorization": f"Bearer {token}"}


# ---------- memory + user-doc ----------


async def test_memory_round_trip(client: AsyncClient) -> None:
    headers = _auth_headers()
    r = await client.get("/v1/users/me/memory", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"content": ""}

    r = await client.put(
        "/v1/users/me/memory",
        headers=headers,
        json={"content": "remember the milk"},
    )
    assert r.status_code == 200

    r = await client.get("/v1/users/me/memory", headers=headers)
    assert r.json() == {"content": "remember the milk"}


async def test_memory_too_long_rejected(client: AsyncClient) -> None:
    headers = _auth_headers()
    r = await client.put(
        "/v1/users/me/memory",
        headers=headers,
        json={"content": "x" * 3000},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["kind"] == "CONFLICT"


async def test_user_doc_round_trip(client: AsyncClient) -> None:
    headers = _auth_headers()
    r = await client.put(
        "/v1/users/me/user-doc",
        headers=headers,
        json={"content": "Loves Python."},
    )
    assert r.status_code == 200
    r = await client.get("/v1/users/me/user-doc", headers=headers)
    assert r.json() == {"content": "Loves Python."}


# ---------- settings ----------


async def test_settings_defaults_then_update(client: AsyncClient) -> None:
    headers = _auth_headers()
    r = await client.get("/v1/users/me/settings", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"default_model": None, "permission_mode": "auto"}

    r = await client.put(
        "/v1/users/me/settings",
        headers=headers,
        json={"default_model": "gpt-5.4", "permission_mode": "ask"},
    )
    assert r.status_code == 200
    assert r.json() == {"default_model": "gpt-5.4", "permission_mode": "ask"}

    r = await client.put(
        "/v1/users/me/settings",
        headers=headers,
        json={"clear_default_model": True},
    )
    assert r.json()["default_model"] is None


# ---------- skills ----------


async def test_skill_crud(client: AsyncClient) -> None:
    headers = _auth_headers()
    body = {
        "slug": "my-skill",
        "frontmatter": {"name": "my-skill", "description": "do the thing"},
        "body": "# my-skill\n",
    }
    r = await client.post("/v1/users/me/skills", headers=headers, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["slug"] == "my-skill"

    # duplicate → 409
    r = await client.post("/v1/users/me/skills", headers=headers, json=body)
    assert r.status_code == 409

    r = await client.get("/v1/users/me/skills", headers=headers)
    assert [s["slug"] for s in r.json()] == ["my-skill"]

    r = await client.put(
        "/v1/users/me/skills/my-skill",
        headers=headers,
        json={"body": "updated body"},
    )
    assert r.status_code == 200
    assert r.json()["body"] == "updated body"

    r = await client.delete("/v1/users/me/skills/my-skill", headers=headers)
    assert r.status_code == 204
    r = await client.get("/v1/users/me/skills/my-skill", headers=headers)
    assert r.status_code == 404


# ---------- instructions ----------


async def test_instruction_crud(client: AsyncClient) -> None:
    headers = _auth_headers()
    r = await client.post(
        "/v1/users/me/instructions",
        headers=headers,
        json={"slug": "tone", "body": "be concise", "priority": 60},
    )
    assert r.status_code == 201
    r = await client.put(
        "/v1/users/me/instructions/tone",
        headers=headers,
        json={"priority": 30},
    )
    assert r.json()["priority"] == 30
    r = await client.delete("/v1/users/me/instructions/tone", headers=headers)
    assert r.status_code == 204


# ---------- commands ----------


async def test_command_crud(client: AsyncClient) -> None:
    headers = _auth_headers()
    r = await client.post(
        "/v1/users/me/commands",
        headers=headers,
        json={"slug": "greet", "prompt_tpl": "Hi {{ name }}"},
    )
    assert r.status_code == 201
    r = await client.get("/v1/users/me/commands/greet", headers=headers)
    assert r.json()["prompt_tpl"] == "Hi {{ name }}"


# ---------- mcp + KMS round-trip ----------


async def test_mcp_create_does_not_leak_secret(client: AsyncClient) -> None:
    """Auth value must be KMS-encrypted at rest, never in responses."""
    headers = _auth_headers()
    body = {
        "slug": "github",
        "url": "https://example.com/mcp",
        "auth_header": "Authorization",
        "auth_value": "Bearer s3cret",
    }
    r = await client.post("/v1/users/me/mcp-servers", headers=headers, json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["has_auth_value"] is True
    # Plaintext must never reach the wire.
    assert "auth_value" not in out
    assert "s3cret" not in r.text

    r = await client.get("/v1/users/me/mcp-servers/github", headers=headers)
    assert r.status_code == 200
    assert "s3cret" not in r.text

    # Effective config must also stay clean.
    r = await client.get("/v1/users/me/effective-config", headers=headers)
    assert "s3cret" not in r.text
    mcps = r.json()["mcp_servers"]
    me = next(m for m in mcps if m["slug"] == "github")
    assert me["has_auth_value"] is True


async def test_mcp_kms_round_trip_decrypts(
    client: AsyncClient, _migrated_pg: str
) -> None:
    """Ciphertext at rest decrypts to the original plaintext via KMS."""
    headers = _auth_headers()
    body = {
        "slug": "with-secret",
        "url": "https://example.com/mcp",
        "auth_header": "X-Api-Key",
        "auth_value": "round-trip-token",
    }
    r = await client.post("/v1/users/me/mcp-servers", headers=headers, json=body)
    assert r.status_code == 201

    # Pull the KMS we injected and the ciphertext we wrote, decrypt
    # through a fresh DB session that doesn't go through the route.
    from sqlalchemy import text

    from lite_horse.storage import db as db_mod

    sessionmaker = db_mod.get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT user_id, auth_value_ct FROM mcp_servers "
                        "WHERE slug='with-secret' AND scope='user'"
                    )
                )
            ).one()
    user_id, ct = str(row[0]), bytes(row[1])
    assert ct, "auth_value_ct must be non-NULL"

    # The KMS we injected lives on app.state via dependency_overrides — pull it
    # back out by calling the override.
    test_kms = client._transport.app.dependency_overrides[get_kms]()
    plain = await test_kms.decrypt(ct, {"user_id": user_id})
    assert plain == b"round-trip-token"


# ---------- cron ----------


async def test_cron_crud(client: AsyncClient) -> None:
    headers = _auth_headers()
    r = await client.post(
        "/v1/users/me/cron-jobs",
        headers=headers,
        json={
            "slug": "morning-brief",
            "cron_expr": "0 9 * * *",
            "prompt": "Summarise overnight events.",
        },
    )
    assert r.status_code == 201
    r = await client.get("/v1/users/me/cron-jobs", headers=headers)
    assert [c["slug"] for c in r.json()] == ["morning-brief"]
    r = await client.delete(
        "/v1/users/me/cron-jobs/morning-brief", headers=headers
    )
    assert r.status_code == 204


# ---------- opt-outs ----------


async def test_opt_out_round_trip(client: AsyncClient) -> None:
    headers = _auth_headers()
    r = await client.post(
        "/v1/users/me/opt-outs",
        headers=headers,
        json={"entity": "skill", "slug": "noisy"},
    )
    assert r.status_code == 201

    r = await client.get("/v1/users/me/opt-outs", headers=headers)
    assert {(o["entity"], o["slug"]) for o in r.json()} == {("skill", "noisy")}

    r = await client.delete("/v1/users/me/opt-outs/skill/noisy", headers=headers)
    assert r.status_code == 204
    r = await client.delete("/v1/users/me/opt-outs/skill/noisy", headers=headers)
    assert r.status_code == 404


# ---------- effective-config ----------


async def test_effective_config_includes_bundled(client: AsyncClient) -> None:
    """Empty-DB users still see bundled `plan`, `safety-baseline`, and
    `explain-stack-trace` from the in-image fixtures."""
    headers = _auth_headers()
    r = await client.get("/v1/users/me/effective-config", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert any(s["slug"] == "plan" for s in body["skills"])
    assert any(i["slug"] == "safety-baseline" for i in body["instructions"])
    assert any(c["slug"] == "explain-stack-trace" for c in body["commands"])
    assert len(body["etag"]) == 16


async def test_effective_config_etag_changes_after_user_skill(
    client: AsyncClient,
) -> None:
    """Acceptance — creating a skill via HTTP shifts the effective-config etag."""
    headers = _auth_headers()
    before = (await client.get(
        "/v1/users/me/effective-config", headers=headers
    )).json()["etag"]

    r = await client.post(
        "/v1/users/me/skills",
        headers=headers,
        json={
            "slug": "via-http",
            "frontmatter": {"name": "via-http"},
            "body": "made over HTTP",
        },
    )
    assert r.status_code == 201

    after = (await client.get(
        "/v1/users/me/effective-config", headers=headers
    )).json()["etag"]
    assert before != after
    skills = (await client.get(
        "/v1/users/me/effective-config", headers=headers
    )).json()["skills"]
    assert any(
        s["slug"] == "via-http" and s["scope"] == "user" for s in skills
    )
