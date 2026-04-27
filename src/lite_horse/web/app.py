"""FastAPI app factory + lifespan.

`create_app()` is the canonical entry-point used by `uvicorn` in
`docker-compose` and by tests. The lifespan eagerly initialises the
async DB engine + Redis client on startup and disposes them on shutdown
so connection pooling is hot from the first request. Phase 34 also
spawns the effective-config invalidation subscriber so admin writes in
another ECS task evict this task's cache within ~1 s.

OTel bootstrap is intentionally a no-op here; full observability lands in
Phase 38.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lite_horse.config import get_settings
from lite_horse.storage.db import dispose_engine, get_engine
from lite_horse.storage.redis_client import make_redis_client
from lite_horse.web.effective_invalidate import run_invalidation_subscriber
from lite_horse.web.routes.admin import router as admin_router
from lite_horse.web.routes.debug import router as debug_router
from lite_horse.web.routes.ops import router as ops_router
from lite_horse.web.routes.user_config import router as user_config_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_engine()
    app.state.redis = make_redis_client()
    app.state.invalidation_task = asyncio.create_task(
        run_invalidation_subscriber(app.state.redis)
    )
    try:
        yield
    finally:
        app.state.invalidation_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await app.state.invalidation_task
        try:
            await app.state.redis.aclose()
        finally:
            await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="lite-horse", lifespan=_lifespan)
    app.include_router(ops_router)
    app.include_router(user_config_router)
    app.include_router(admin_router)
    if settings.env == "local":
        app.include_router(debug_router)
    return app
