"""Provider abstraction for lite-horse v0.4.

Cloud turns route through a :class:`~lite_horse.providers.base.ModelProvider`
selected from the user's chosen model. The provider knows how to construct
an SDK-compatible model client given a per-user API key (BYO) and how
much a given (input, cached_input, output) token tuple costs.

Selection happens via :func:`provider_for_model`: the first provider whose
:meth:`ModelProvider.matches` returns True wins. The OpenAI-compatible
passthrough (``oai/`` prefix) is registered first so its explicit prefix
wins over the more permissive OpenAI ``o*`` matcher.
"""
from __future__ import annotations

from lite_horse.providers.anthropic import AnthropicProvider
from lite_horse.providers.base import ModelProvider, ProviderName
from lite_horse.providers.openai import OpenAIProvider
from lite_horse.providers.openai_compat import OpenAICompatibleProvider
from lite_horse.providers.pricing import (
    PricingTable,
    compute_cost_usd_micro,
    get_pricing_table,
)

__all__ = [
    "AnthropicProvider",
    "ModelProvider",
    "OpenAICompatibleProvider",
    "OpenAIProvider",
    "PricingTable",
    "ProviderName",
    "compute_cost_usd_micro",
    "get_pricing_table",
    "provider_for_model",
    "registered_providers",
]


_REGISTRY: list[ModelProvider] = [
    OpenAICompatibleProvider(),
    OpenAIProvider(),
    AnthropicProvider(),
]


def registered_providers() -> list[ModelProvider]:
    """Return the configured provider list (ordered)."""
    return list(_REGISTRY)


def provider_for_model(model: str) -> ModelProvider:
    """Resolve the first provider whose :meth:`matches` accepts ``model``."""
    for provider in _REGISTRY:
        if provider.matches(model):
            return provider
    raise ValueError(f"no provider registered for model {model!r}")
