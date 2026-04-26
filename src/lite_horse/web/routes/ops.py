"""Liveness + readiness probes (no auth).

* `/v1/health` — fast liveness; returns 200 unconditionally.
* `/v1/ready` — readiness; pings DB and Redis. Either failure → 503.

`check_db` and `check_redis` are exported as deps so unit tests can override
them without standing up Postgres/Redis.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from lite_horse.storage.db import ping_db
from lite_horse.storage.redis_client import ping_redis
from lite_horse.web.errors import ErrorKind, http_error

router = APIRouter(prefix="/v1", tags=["ops"])


async def check_db() -> bool:
    try:
        await ping_db()
    except Exception as exc:
        raise http_error(ErrorKind.UNAVAILABLE, f"db unreachable: {exc}") from exc
    return True


async def check_redis() -> bool:
    try:
        await ping_redis()
    except Exception as exc:
        raise http_error(ErrorKind.UNAVAILABLE, f"redis unreachable: {exc}") from exc
    return True


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    _db: Annotated[bool, Depends(check_db)],
    _redis: Annotated[bool, Depends(check_redis)],
) -> dict[str, str]:
    return {"status": "ready"}
