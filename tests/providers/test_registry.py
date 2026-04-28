"""Registry: provider_for_model picks first match."""
from __future__ import annotations

import pytest

from lite_horse.providers import (
    AnthropicProvider,
    OpenAIProvider,
    provider_for_model,
    registered_providers,
)


def test_openai_matches_gpt() -> None:
    p = provider_for_model("gpt-5.4")
    assert isinstance(p, OpenAIProvider)


def test_openai_matches_o_series() -> None:
    p = provider_for_model("o3-mini")
    assert isinstance(p, OpenAIProvider)


def test_anthropic_matches_claude() -> None:
    p = provider_for_model("claude-sonnet-4-6")
    assert isinstance(p, AnthropicProvider)


def test_unknown_model_raises() -> None:
    with pytest.raises(ValueError):
        provider_for_model("mistral-7b")


def test_registered_providers_returns_copy() -> None:
    a = registered_providers()
    b = registered_providers()
    assert a is not b
    assert [type(p) for p in a] == [type(p) for p in b]


def test_anthropic_build_model_uses_openai_compat() -> None:
    provider = AnthropicProvider()
    model = provider.build_model("claude-sonnet-4-6", "test-key")
    # The SDK model wraps an AsyncOpenAI client; check the base_url.
    client = model._client  # type: ignore[attr-defined]
    assert "api.anthropic.com" in str(client.base_url)


def test_openai_build_model_default_base_url() -> None:
    provider = OpenAIProvider()
    model = provider.build_model("gpt-5.4", "test-key")
    client = model._client  # type: ignore[attr-defined]
    # Default base_url contains api.openai.com.
    assert "api.openai.com" in str(client.base_url)
