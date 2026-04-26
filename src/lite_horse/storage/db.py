"""Async DB engine + session factory + per-tenant GUC setter.

Per the v0.4 Hard Contract:

* `AsyncEngine` is a single module-level singleton per process,
  `pool_size=5, max_overflow=5, pool_pre_ping=True, pool_recycle=1800`.
* Every Postgres connection sets `app.user_id` via
  `SELECT set_config('app.user_id', $1, true)` on session checkout. RLS
  policies enforce tenant isolation as defence-in-depth.

`db_session(user_id)` is the canonical request-scoped dependency — it
opens a transaction, sets the GUC for the duration of that transaction,
yields the `AsyncSession`, and commits/rolls back in `finally`. The
FastAPI wiring (which reads `user_id` from a `RequestContext` ContextVar)
lands in Phase 31c.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from lite_horse.config import get_settings


def make_async_engine(url: str | None = None) -> AsyncEngine:
    """Construct an `AsyncEngine` with the Hard Contract pool sizing."""
    settings = get_settings()
    return create_async_engine(
        url or settings.database_url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )


@dataclass
class _EngineHolder:
    engine: AsyncEngine | None = None
    sessionmaker: async_sessionmaker[AsyncSession] | None = None


_state = _EngineHolder()


def get_engine() -> AsyncEngine:
    """Lazy module-level singleton engine. First call sizes the pool."""
    if _state.engine is None:
        _state.engine = make_async_engine()
        _state.sessionmaker = async_sessionmaker(_state.engine, expire_on_commit=False)
    return _state.engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _state.sessionmaker is not None
    return _state.sessionmaker


async def dispose_engine() -> None:
    """Close the engine; called on app shutdown."""
    if _state.engine is not None:
        await _state.engine.dispose()
        _state.engine = None
        _state.sessionmaker = None


def reset_engine_for_tests() -> None:
    """Reset the singleton — tests only."""
    _state.engine = None
    _state.sessionmaker = None
    get_settings.cache_clear()


@asynccontextmanager
async def db_session(user_id: str | None) -> AsyncIterator[AsyncSession]:
    """Open a transaction, set `app.user_id` GUC, yield the session.

    Pass `user_id=None` only for genuinely tenant-less work (admin
    bootstrap, migrations). RLS policies use
    `current_setting('app.user_id', true)` and treat NULL as "no tenant".
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            if user_id is not None:
                await session.execute(
                    text("SELECT set_config('app.user_id', :uid, true)"),
                    {"uid": str(user_id)},
                )
            yield session
