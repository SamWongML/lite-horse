"""EnvSecretsProvider contract."""
from __future__ import annotations

import pytest

from lite_horse.storage.secrets import SecretsProvider
from lite_horse.storage.secrets_env import EnvSecretsProvider


async def test_satisfies_protocol() -> None:
    assert isinstance(EnvSecretsProvider(), SecretsProvider)


async def test_get_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    provider = EnvSecretsProvider()
    assert await provider.get("openai-api-key") == "sk-test"


async def test_overrides_win_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PASSWORD", "from-env")
    provider = EnvSecretsProvider({"db_password": "from-override"})
    assert await provider.get("db_password") == "from-override"


async def test_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOPE_NOT_SET", raising=False)
    with pytest.raises(KeyError):
        await EnvSecretsProvider().get("nope-not-set")
