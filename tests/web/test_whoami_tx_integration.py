"""Phase 31 acceptance: a JWT request lands a `users` row, populates
`RequestContext`, and sets `current_setting('app.user_id')` inside the
request transaction.

Skipped if Docker isn't reachable; full coverage runs under `make test-int`
or in CI.
"""
from __future__ import annotations

import base64
import time
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

from lite_horse.web import auth as auth_mod

pytestmark = pytest.mark.integration

_SECRET = b"int-test-secret-do-not-use-in-prod"
_KID = "int-kid-1"
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


def _mint(sub: str, role: str = "user") -> str:
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
def pg_url() -> Iterator[str]:
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # docker not reachable → skip
        pytest.skip(f"Docker unavailable: {exc!r}")

    raw_url = container.get_connection_url()
    async_url = raw_url.replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")
    try:
        yield async_url
    finally:
        container.stop()


async def test_whoami_tx_creates_user_and_sets_guc(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITEHORSE_DATABASE_URL", pg_url)
    monkeypatch.setenv("LITEHORSE_ENV", "local")

    from lite_horse.config import get_settings
    from lite_horse.storage import db as db_mod

    get_settings.cache_clear()
    db_mod.reset_engine_for_tests()

    # Apply migrations.
    import os
    import subprocess
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["LITEHORSE_DATABASE_URL"] = pg_url
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

    async def fake_jwks(_url: str) -> dict[str, Any]:
        return _jwks()

    monkeypatch.setattr(auth_mod, "_get_jwks", fake_jwks)
    auth_mod._clear_jwks_cache()

    from lite_horse.web import create_app

    app = create_app()
    token = _mint(sub="acceptance-user-1", role="user")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get(
            "/v1/_debug/whoami-tx",
            headers={"Authorization": f"Bearer {token}"},
        )

    await db_mod.dispose_engine()
    assert resp.status_code == 200
    body = resp.json()
    assert body["external_id"] == "acceptance-user-1"
    assert body["role"] == "user"
    # GUC inside the transaction equals the lazy-created user UUID.
    assert body["app_user_id_guc"] == body["user_id"]
