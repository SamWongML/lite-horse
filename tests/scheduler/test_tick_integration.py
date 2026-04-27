"""Integration test for :func:`lite_horse.scheduler.tick.tick`.

Spins up Postgres via testcontainers, applies migrations, seeds a
handful of cron rows + users (committed), runs one tick against an
:class:`InMemoryMessageQueue`, and asserts the right per-user fan-out
plus ``last_fired_at`` bookkeeping.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from lite_horse.cron.scheduler import CronMessage
from lite_horse.scheduler.tick import tick
from lite_horse.storage.queue_memory import InMemoryMessageQueue

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


@pytest_asyncio.fixture()
async def _engine(
    _migrated_pg: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    monkeypatch.setenv("LITEHORSE_DATABASE_URL", _migrated_pg)
    monkeypatch.setenv("LITEHORSE_ENV", "local")
    from lite_horse.config import get_settings
    from lite_horse.storage import db as db_mod

    get_settings.cache_clear()
    db_mod.reset_engine_for_tests()
    yield
    await db_mod.dispose_engine()


@pytest_asyncio.fixture()
async def _seeded(_engine: None) -> AsyncIterator[dict[str, object]]:
    """Commit a small fixture set: 2 users, 1 user-scope + 1 official cron."""
    from lite_horse.storage import db as db_mod

    sm = db_mod.get_sessionmaker()
    user_a = uuid4()
    user_b = uuid4()
    user_cron_id = uuid4()
    official_cron_id = uuid4()

    async with sm() as session:
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO users (id, external_id, role) "
                    "VALUES (:a, :ea, 'user'), (:b, :eb, 'user')"
                ),
                {
                    "a": user_a,
                    "ea": f"sch-test-{user_a.hex[:8]}",
                    "b": user_b,
                    "eb": f"sch-test-{user_b.hex[:8]}",
                },
            )
            await session.execute(
                text(
                    "INSERT INTO cron_jobs "
                    "(id, scope, user_id, slug, cron_expr, prompt, "
                    " webhook_url, enabled, mandatory, strikes) "
                    "VALUES (:id, 'user', :uid, 'standup', '* * * * *', "
                    " 'do the thing', 'https://hook/standup', true, false, 0)"
                ),
                {"id": user_cron_id, "uid": user_a},
            )
            await session.execute(
                text(
                    "INSERT INTO cron_jobs "
                    "(id, scope, user_id, slug, cron_expr, prompt, "
                    " webhook_url, enabled, mandatory, strikes) "
                    "VALUES (:id, 'official', NULL, 'weekly', '* * * * *', "
                    " 'corp announcement', NULL, true, false, 0)"
                ),
                {"id": official_cron_id},
            )
    try:
        yield {
            "user_a": user_a,
            "user_b": user_b,
            "user_cron_id": user_cron_id,
            "official_cron_id": official_cron_id,
        }
    finally:
        async with sm() as session:
            async with session.begin():
                await session.execute(
                    text("DELETE FROM cron_jobs WHERE id IN (:a, :b)"),
                    {"a": user_cron_id, "b": official_cron_id},
                )
                await session.execute(
                    text("DELETE FROM users WHERE id IN (:a, :b)"),
                    {"a": user_a, "b": user_b},
                )


async def test_tick_fans_user_and_official(
    _seeded: dict[str, object],
) -> None:
    queue = InMemoryMessageQueue()
    moment = datetime(2026, 4, 28, 9, 0, tzinfo=UTC)

    enqueued = await tick(queue=queue, now=moment)

    # 1 user-scope + 2 active users * 1 official-scope job = 3 messages.
    assert enqueued == 3
    received = await queue.receive(max_messages=10, wait_seconds=0)
    assert len(received) == 3
    parsed = [CronMessage.from_json(r.body) for r in received]
    user_msgs = [m for m in parsed if m.scope == "user"]
    official_msgs = [m for m in parsed if m.scope == "official"]
    assert len(user_msgs) == 1
    assert len(official_msgs) == 2
    assert {m.user_id for m in official_msgs} == {
        str(_seeded["user_a"]),
        str(_seeded["user_b"]),
    }


async def test_tick_marks_jobs_fired(
    _seeded: dict[str, object],
) -> None:
    queue = InMemoryMessageQueue()
    moment = datetime(2026, 4, 28, 9, 0, tzinfo=UTC)

    await tick(queue=queue, now=moment)

    from lite_horse.storage import db as db_mod

    sm = db_mod.get_sessionmaker()
    async with sm() as session:
        async with session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT last_fired_at FROM cron_jobs "
                        "WHERE id = :id"
                    ),
                    {"id": _seeded["user_cron_id"]},
                )
            ).scalar_one()
    assert row == moment


async def test_second_tick_inside_window_no_double_fire(
    _seeded: dict[str, object],
) -> None:
    queue = InMemoryMessageQueue()
    moment = datetime(2026, 4, 28, 9, 0, tzinfo=UTC)

    first = await tick(queue=queue, now=moment)
    assert first == 3
    # Second tick at the SAME minute boundary — last_fired_at == now,
    # so the next fire is not yet due. Nothing enqueued.
    second = await tick(queue=queue, now=moment)
    assert second == 0
