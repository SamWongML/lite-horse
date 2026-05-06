"""`make_session_lock` env switch — local returns the in-memory impl."""
from __future__ import annotations

import pytest

from lite_horse.config import Settings, get_settings
from lite_horse.storage import make_session_lock
from lite_horse.storage.locks import SessionLock
from lite_horse.storage.locks_memory import InMemorySessionLock


def test_local_env_returns_in_memory_impl() -> None:
    assert get_settings().env == "local"
    lock = make_session_lock()
    assert isinstance(lock, InMemorySessionLock)
    assert isinstance(lock, SessionLock)


def test_non_local_env_returns_redis_impl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-local env builds a RedisSessionLock around the supplied client."""
    cloud = Settings(env="dev", redis_url="redis://example.invalid:6379/0")
    monkeypatch.setattr(
        "lite_horse.config.get_settings", lambda: cloud, raising=True
    )
    # Sentinel client — RedisSessionLock should accept it as-is without
    # touching the network during construction.
    sentinel = object()
    lock = make_session_lock(sentinel)  # type: ignore[arg-type]
    from lite_horse.storage.locks_redis import RedisSessionLock

    assert isinstance(lock, RedisSessionLock)
    assert isinstance(lock, SessionLock)
