"""Redis client factory + readiness ping.

Lives inside `storage/` because the import boundary forbids `redis`
imports anywhere else (see `tests/lint/test_storage_import_boundary.py`).
"""
from __future__ import annotations

from redis.asyncio import Redis

from lite_horse.config import get_settings


def make_redis_client(url: str | None = None) -> Redis:
    target = url or get_settings().redis_url
    client: Redis = Redis.from_url(target, decode_responses=True)
    return client


async def ping_redis(url: str | None = None) -> None:
    client = make_redis_client(url)
    try:
        await client.ping()
    finally:
        await client.aclose()
