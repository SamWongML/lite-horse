"""OpenAI-compatible passthrough provider: routing, base_url, prefix strip."""
from __future__ import annotations

import pytest

from lite_horse.providers import (
    OpenAICompatibleProvider,
    OpenAIProvider,
    provider_for_model,
    registered_providers,
)
from lite_horse.providers.openai_compat import COMPAT_BASE_URL_ENV


def test_matches_oai_prefix() -> None:
    p = OpenAICompatibleProvider()
    assert p.matches("oai/azure-gpt-4o-mini")
    assert p.matches("oai/llama-3.1-70b")
    assert not p.matches("gpt-5.4")
    assert not p.matches("claude-sonnet-4-6")
    assert not p.matches("o3-mini")


def test_provider_name_is_openai_compat() -> None:
    # The name string is the lookup key into BYO storage and the
    # turn_engine env fallback map — pin it explicitly.
    assert OpenAICompatibleProvider().name == "openai_compat"


def test_turn_engine_env_fallback_registered() -> None:
    from lite_horse.web.turn_engine import _PROVIDER_ENV_FALLBACK

    assert _PROVIDER_ENV_FALLBACK["openai_compat"] == "OPENAI_COMPAT_API_KEY"


def test_registry_routes_oai_prefix_to_compat() -> None:
    p = provider_for_model("oai/azure-gpt-4o-mini")
    assert isinstance(p, OpenAICompatibleProvider)


def test_registry_still_routes_native_openai() -> None:
    # The compat provider's oai/ prefix must not steal o-series OpenAI models.
    p = provider_for_model("o3-mini")
    assert isinstance(p, OpenAIProvider)


def test_registry_order_puts_compat_before_openai() -> None:
    providers = registered_providers()
    types = [type(p).__name__ for p in providers]
    assert types.index("OpenAICompatibleProvider") < types.index("OpenAIProvider")


def test_build_model_uses_explicit_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(COMPAT_BASE_URL_ENV, raising=False)
    p = OpenAICompatibleProvider()
    model = p.build_model(
        "oai/azure-gpt-4o-mini",
        "test-key",
        base_url="https://my-azure.openai.azure.com/openai/v1/",
    )
    client = model._client  # type: ignore[attr-defined]
    assert "my-azure.openai.azure.com" in str(client.base_url)


def test_build_model_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(COMPAT_BASE_URL_ENV, "http://localhost:4000/v1/")
    p = OpenAICompatibleProvider()
    model = p.build_model("oai/anything", "test-key")
    client = model._client  # type: ignore[attr-defined]
    assert "localhost:4000" in str(client.base_url)


def test_build_model_strips_prefix_before_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(COMPAT_BASE_URL_ENV, "http://localhost:4000/v1/")
    p = OpenAICompatibleProvider()
    model = p.build_model("oai/llama-3.1-70b", "test-key")
    # The SDK model stores the upstream-facing name on .model.
    assert model.model == "llama-3.1-70b"  # type: ignore[attr-defined]


def test_build_model_without_base_url_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(COMPAT_BASE_URL_ENV, raising=False)
    p = OpenAICompatibleProvider()
    with pytest.raises(ValueError, match="base_url"):
        p.build_model("oai/anything", "test-key")


def test_pricing_lookup_strips_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    # gpt-5.4 has a pricing row; ensure oai/gpt-5.4 resolves it via prefix strip.
    p = OpenAICompatibleProvider()
    row = p.pricing("oai/gpt-5.4")
    assert row is not None
    assert row.name == "gpt-5.4"


def test_pricing_unknown_returns_none() -> None:
    p = OpenAICompatibleProvider()
    assert p.pricing("oai/some-random-local-model") is None
