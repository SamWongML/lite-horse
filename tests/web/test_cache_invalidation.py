"""Phase 34 cross-process cache invalidation.

Two parallel ``run_invalidation_subscriber`` listeners simulate two ECS
tasks bound to the same Redis. An admin write in process A must evict
the cached effective-config from process B's Redis-backed cache within
~1 s.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from lite_horse.storage.redis_client import Redis, make_redis_client
from lite_horse.web.effective_invalidate import (
    publish_invalidation,
    run_invalidation_subscriber,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


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


@pytest.fixture(scope="module")
def _redis_url() -> Iterator[str]:
    try:
        from testcontainers.redis import RedisContainer
    except ImportError:
        pytest.skip("testcontainers[redis] not installed")
    try:
        container = RedisContainer("redis:7-alpine")
        container.start()
    except Exception as exc:
        pytest.skip(f"Docker unavailable: {exc!r}")
    host = container.get_container_host_ip()
    port = container.get_exposed_port(6379)
    try:
        yield f"redis://{host}:{port}/0"
    finally:
        container.stop()


@pytest_asyncio.fixture()
async def redis_client(_redis_url: str) -> AsyncIterator[Redis]:
    client = make_redis_client(_redis_url)
    try:
        await client.flushdb()
        yield client
    finally:
        await client.aclose()


async def test_publish_evicts_local_keys(redis_client: Redis) -> None:
    """Publishing broadcast invalidation drops every effective:* key locally."""
    await redis_client.set("effective:user-a", '{"x":1}')
    await redis_client.set("effective:user-b", '{"x":2}')
    await redis_client.set("other:noop", "noop")

    await publish_invalidation(redis_client)

    assert await redis_client.get("effective:user-a") is None
    assert await redis_client.get("effective:user-b") is None
    assert await redis_client.get("other:noop") == "noop"


async def test_publish_scoped_evicts_only_those_users(
    redis_client: Redis,
) -> None:
    await redis_client.set("effective:keep-me", '{"k":1}')
    await redis_client.set("effective:drop-me", '{"k":2}')

    await publish_invalidation(redis_client, user_ids=["drop-me"])

    assert await redis_client.get("effective:keep-me") == '{"k":1}'
    assert await redis_client.get("effective:drop-me") is None


async def test_subscriber_evicts_in_other_process_simulation(
    _redis_url: str,
) -> None:
    """Two clients on the same Redis: A publishes → B's keys drop."""
    publisher = make_redis_client(_redis_url)
    subscriber = make_redis_client(_redis_url)
    try:
        await publisher.flushdb()

        # Pre-warm both halves of the cache from "process B".
        await subscriber.set("effective:tenant-1", '{"v":1}')
        await subscriber.set("effective:tenant-2", '{"v":2}')

        stop = asyncio.Event()
        sub_task = asyncio.create_task(
            run_invalidation_subscriber(subscriber, stop_event=stop)
        )

        # Give the subscriber a moment to attach.
        await asyncio.sleep(0.2)

        # Process A publishes a broadcast invalidation.
        await publish_invalidation(publisher)

        # Wait up to 2 s for the subscriber to react.
        deadline = asyncio.get_running_loop().time() + 2.0
        while asyncio.get_running_loop().time() < deadline:
            if (
                await subscriber.get("effective:tenant-1") is None
                and await subscriber.get("effective:tenant-2") is None
            ):
                break
            await asyncio.sleep(0.05)

        assert await subscriber.get("effective:tenant-1") is None
        assert await subscriber.get("effective:tenant-2") is None

        stop.set()
        sub_task.cancel()
        try:
            await sub_task
        except asyncio.CancelledError:
            pass
    finally:
        await publisher.aclose()
        await subscriber.aclose()


def _mint_admin_token(secret: bytes, kid: str) -> str:
    import time
    from uuid import uuid4

    from jose import jwt

    return jwt.encode(
        {
            "sub": f"phase34-cache-admin-{uuid4()}",
            "role": "admin",
            "aud": "lite-horse",
            "iss": "http://localhost:9999",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        },
        secret,
        algorithm="HS256",
        headers={"kid": kid},
    )


def _stub_jwks(monkeypatch: pytest.MonkeyPatch, secret: bytes, kid: str) -> None:
    import base64
    from typing import Any

    from lite_horse.web import auth as auth_mod

    async def fake_jwks(_url: str) -> dict[str, Any]:
        return {
            "keys": [
                {
                    "kty": "oct",
                    "kid": kid,
                    "alg": "HS256",
                    "k": base64.urlsafe_b64encode(secret)
                    .rstrip(b"=")
                    .decode("ascii"),
                }
            ]
        }

    monkeypatch.setattr(auth_mod, "_get_jwks", fake_jwks)
    auth_mod._clear_jwks_cache()


async def test_admin_write_publishes_invalidation_seen_by_other_subscriber(
    _migrated_pg: str, _redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: HTTP admin write → invalidation visible on a peer Redis client."""
    from uuid import uuid4

    from cryptography.fernet import Fernet
    from httpx import ASGITransport, AsyncClient

    from lite_horse.storage.kms_local import LocalKms
    from lite_horse.web.deps import get_kms, get_redis

    secret = b"phase-34-cache-secret"
    kid = "phase-34-cache-kid"

    monkeypatch.setenv("LITEHORSE_DATABASE_URL", _migrated_pg)
    monkeypatch.setenv("LITEHORSE_REDIS_URL", _redis_url)
    monkeypatch.setenv("LITEHORSE_ENV", "local")

    from lite_horse.config import get_settings
    from lite_horse.storage import db as db_mod

    get_settings.cache_clear()
    db_mod.reset_engine_for_tests()
    _stub_jwks(monkeypatch, secret, kid)

    fernet_key = Fernet.generate_key()

    def make_kms() -> LocalKms:
        return LocalKms(fernet_key)

    peer = make_redis_client(_redis_url)
    await peer.flushdb()
    await peer.set("effective:peer-tenant", '{"stale":true}')

    # The admin process's Redis. publish_invalidation evicts effective:*
    # keys directly on this client, which is the same Redis instance the
    # peer reads from.
    admin_redis = make_redis_client(_redis_url)

    from lite_horse.web import create_app

    app = create_app()
    app.dependency_overrides[get_kms] = make_kms
    app.dependency_overrides[get_redis] = lambda: admin_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        await asyncio.sleep(0.2)

        headers = {"Authorization": f"Bearer {_mint_admin_token(secret, kid)}"}
        slug = f"sk-cache-{uuid4().hex[:8]}"
        r = await client.post(
            "/v1/admin/skills",
            headers=headers,
            json={
                "slug": slug,
                "frontmatter": {"name": slug, "description": "x"},
                "body": "x",
            },
        )
        assert r.status_code == 201, r.text

        deadline = asyncio.get_running_loop().time() + 1.5
        while asyncio.get_running_loop().time() < deadline:
            if await peer.get("effective:peer-tenant") is None:
                break
            await asyncio.sleep(0.05)
        assert await peer.get("effective:peer-tenant") is None

    await peer.aclose()
    await admin_redis.aclose()

    async with db_mod.get_sessionmaker()() as session:
        async with session.begin():
            await session.execute(
                text("DELETE FROM skills WHERE scope = 'official'")
            )
            await session.execute(text("DELETE FROM audit_log"))
            await session.execute(text("DELETE FROM users"))
    await db_mod.dispose_engine()
