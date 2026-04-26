"""Settings (pydantic-settings) loads cloud config from LITEHORSE_* env vars."""
from __future__ import annotations

import pytest

from lite_horse.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    get_settings.cache_clear()


def test_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.env == "local"
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.redis_url.startswith("redis://")


def test_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITEHORSE_ENV", "prod")
    monkeypatch.setenv("LITEHORSE_DATABASE_URL", "postgresql+asyncpg://prod/db")
    monkeypatch.setenv("LITEHORSE_REDIS_URL", "redis://prod:6379/0")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.env == "prod"
    assert s.database_url == "postgresql+asyncpg://prod/db"
    assert s.redis_url == "redis://prod:6379/0"


def test_get_settings_is_cached() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b
