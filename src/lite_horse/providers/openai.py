"""OpenAI provider — wraps ``openai.AsyncOpenAI`` for the SDK."""
from __future__ import annotations

from agents.models.interface import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from lite_horse.providers.base import ProviderName
from lite_horse.providers.pricing import ModelPricing, get_pricing_table


class OpenAIProvider:
    """Concrete OpenAI provider.

    Owns model names that start with ``gpt-`` or ``o`` (the o1/o3 series).
    Other prefixes pass through to the next provider in the registry.
    """

    name: ProviderName = "openai"

    def matches(self, model: str) -> bool:
        return model.startswith("gpt-") or model.startswith("o")

    def build_model(
        self, model: str, api_key: str, *, base_url: str | None = None
    ) -> Model:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        return OpenAIChatCompletionsModel(model=model, openai_client=client)

    def pricing(self, model: str) -> ModelPricing | None:
        return get_pricing_table().get(model)
