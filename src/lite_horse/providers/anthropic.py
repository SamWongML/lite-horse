"""Anthropic provider — routes through Anthropic's OpenAI-compat endpoint.

Anthropic exposes an OpenAI-compatible chat-completions API at
``https://api.anthropic.com/v1/`` (see Anthropic docs §"OpenAI SDK
compatibility"). This lets us reuse the SDK's
:class:`OpenAIChatCompletionsModel` without writing a second model
adapter.

The full Anthropic-native client lives in this same module so future
phases can swap to a richer adapter (prompt caching headers,
beta-features) without changing the Protocol.
"""
from __future__ import annotations

from agents.models.interface import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from lite_horse.providers.base import ProviderName
from lite_horse.providers.pricing import ModelPricing, get_pricing_table

ANTHROPIC_OPENAI_COMPAT_URL = "https://api.anthropic.com/v1/"


class AnthropicProvider:
    """Concrete Anthropic provider via OpenAI-compat endpoint."""

    name: ProviderName = "anthropic"

    def matches(self, model: str) -> bool:
        return model.startswith("claude-")

    def build_model(
        self, model: str, api_key: str, *, base_url: str | None = None
    ) -> Model:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or ANTHROPIC_OPENAI_COMPAT_URL,
        )
        return OpenAIChatCompletionsModel(model=model, openai_client=client)

    def pricing(self, model: str) -> ModelPricing | None:
        return get_pricing_table().get(model)
