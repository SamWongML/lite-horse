"""Pub/sub cache invalidation for ``effective-config``.

Admin writes (Phase 34) propagate to other ECS tasks through Redis
pub/sub on the channel ``effective-config-invalidate``. Payload shape:

* ``{"all": true}`` — invalidate every cached user (used when an admin
  pushes/edits/rolls back an official entity that could affect anyone).
* ``{"user_ids": ["…"]}`` — scoped invalidation (reserved for future
  per-user admin actions; currently unused).

The publish path also evicts matching Redis keys directly so the
admin's own process is consistent without waiting for the round-trip.
The subscriber loop is started by the FastAPI lifespan and stops on
shutdown.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from lite_horse.storage.redis_client import Redis

CHANNEL = "effective-config-invalidate"
_KEY_PREFIX = "effective:"

_log = logging.getLogger(__name__)


async def evict_all(redis: Redis) -> int:
    """Delete every ``effective:*`` key in Redis. Returns # deleted."""
    deleted = 0
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match=f"{_KEY_PREFIX}*", count=500)
        if keys:
            deleted += await redis.delete(*keys)
        if cursor == 0:
            break
    return deleted


async def evict_users(redis: Redis, user_ids: list[str]) -> int:
    if not user_ids:
        return 0
    keys = [f"{_KEY_PREFIX}{uid}" for uid in user_ids]
    deleted: int = await redis.delete(*keys)
    return deleted


async def publish_invalidation(
    redis: Redis | None,
    *,
    user_ids: list[str] | None = None,
) -> None:
    """Evict locally and publish the invalidation message.

    ``user_ids=None`` means broadcast (every cached user). Pass an empty
    list explicitly to no-op (useful when the admin write turned out not
    to mutate state).
    """
    if redis is None:
        return
    payload: dict[str, Any]
    if user_ids is None:
        payload = {"all": True}
        await evict_all(redis)
    else:
        if not user_ids:
            return
        payload = {"user_ids": user_ids}
        await evict_users(redis, user_ids)
    await redis.publish(CHANNEL, json.dumps(payload))


async def run_invalidation_subscriber(
    redis: Redis, *, stop_event: asyncio.Event | None = None
) -> None:
    """Long-running task: listen on the channel, evict on each message.

    Designed to be launched as ``asyncio.create_task`` from the FastAPI
    lifespan. ``stop_event`` lets tests cleanly shut the loop down; in
    production the task is cancelled by the lifespan.
    """
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message is None:
                continue
            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError, KeyError):
                _log.warning("invalidation message dropped: %r", message)
                continue
            if data.get("all"):
                await evict_all(redis)
            elif "user_ids" in data:
                await evict_users(redis, list(data["user_ids"]))
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await pubsub.unsubscribe(CHANNEL)
        finally:
            await pubsub.aclose()  # type: ignore[no-untyped-call]
