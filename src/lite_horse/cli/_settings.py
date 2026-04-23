"""CLI settings cascade — flag > env > config.yaml > default.

Only fields the CLI surfaces directly live here. Full agent/model config
still lives in `lite_horse.config.Config` and is loaded on demand by the
commands that need it. Keeping the two separate avoids duplicating the
pydantic model tree and keeps `--help` fast.

This module imports `pydantic_settings` at load time, so commands must
import it lazily (inside the function body), not at module top.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from lite_horse.constants import litehorse_home


class LitehorseSettings(BaseSettings):
    """Process-level CLI knobs.

    Values resolve in this order:
        1. constructor kwargs (used by tests, or wired from Click flags)
        2. environment: LITEHORSE_<FIELD>
        3. ~/.litehorse/.env
        4. defaults below
    """

    model_config = SettingsConfigDict(
        env_prefix="LITEHORSE_",
        env_file=None,  # set dynamically in `load()` so litehorse_home() wins
        extra="ignore",
    )

    debug: bool = False
    json_output: bool = False
    structured_logs: bool = False


def load() -> LitehorseSettings:
    """Instantiate settings, honoring `~/.litehorse/.env` if present."""
    env_file = litehorse_home() / ".env"
    if env_file.exists():
        return LitehorseSettings(_env_file=str(env_file))  # type: ignore[call-arg]
    return LitehorseSettings()


def state_dir() -> Path:
    """Where the CLI writes state (history, logs, config, cache).

    Thin wrapper around `litehorse_home()` so CLI callers don't reach into
    `lite_horse.constants` directly.
    """
    return litehorse_home()
