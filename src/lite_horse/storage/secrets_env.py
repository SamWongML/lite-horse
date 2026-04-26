"""Env-backed SecretsProvider — local dev + unit tests."""
from __future__ import annotations

import os

from lite_horse.storage.secrets import SecretsProvider


class EnvSecretsProvider(SecretsProvider):
    """Reads secrets from environment variables.

    Maps logical names to env var names by uppercasing and replacing
    non-alphanumerics with `_`. An optional in-memory override map wins
    over env (used by tests).
    """

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._overrides = dict(overrides or {})

    @staticmethod
    def _envify(name: str) -> str:
        return "".join(c if c.isalnum() else "_" for c in name).upper()

    async def get(self, name: str) -> str:
        if name in self._overrides:
            return self._overrides[name]
        env_name = self._envify(name)
        value = os.environ.get(env_name)
        if value is None:
            raise KeyError(f"secret not found: {name!r} (env {env_name})")
        return value
