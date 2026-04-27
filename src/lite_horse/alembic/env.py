"""Alembic environment for lite-horse v0.4.

Async-driver-aware: builds a SQLAlchemy AsyncEngine, runs migrations
inside a synchronous Alembic context driven from the async connection.

Per the v0.4 Hard Contract every migration runs under a Postgres
advisory lock so concurrent ECS task starts serialise. The lock is
held for the duration of `run_migrations()` and released on exit.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from lite_horse.config import get_settings
from lite_horse.models import Base

# 0x6C69_7465_686F_7273 = ASCII bytes for "litehors" interpreted as a bigint.
# Stable across processes; well below the int8 max so PG accepts it as a
# single-arg pg_advisory_lock key.
MIGRATION_LOCK_KEY = 0x6C69_7465_686F_7273

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Ensure the URL is sourced from LITEHORSE_DATABASE_URL via Settings —
# alembic.ini intentionally does not pin one.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without a live DB connection ('alembic upgrade --sql')."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    connection.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
    try:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.execute(
            text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY}
        )


async def run_migrations_online() -> None:
    section: dict[str, Any] = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = settings.database_url
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    async with connectable.connect() as conn:
        await conn.run_sync(_do_run_migrations)
        await conn.commit()
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
