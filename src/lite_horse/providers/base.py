"""``ModelProvider`` Protocol — the contract every model vendor implements.

A provider knows three things:

1. Which model names it owns (``matches``).
2. How to build a v0.14 OpenAI Agents SDK ``Model`` instance bound to a
   given API key (``build_model``). Anthropic plugs in via Anthropic's
   OpenAI-compatible endpoint, so the SDK only ever sees one shape.
3. The token-pricing row for a given model (``pricing``).

The Protocol is deliberately small: anything provider-specific (token
extractor, request shaping) lives elsewhere.
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from agents.models.interface import Model

from lite_horse.providers.pricing import ModelPricing

ProviderName = Literal["openai", "anthropic"]


@runtime_checkable
class ModelProvider(Protocol):
    """Construct SDK ``Model`` instances + carry pricing knowledge."""

    name: ProviderName

    def matches(self, model: str) -> bool:
        """Return True if this provider owns ``model``."""

    def build_model(
        self, model: str, api_key: str, *, base_url: str | None = None
    ) -> Model:
        """Build an SDK ``Model`` bound to ``api_key`` (and optional override URL)."""

    def pricing(self, model: str) -> ModelPricing | None:
        """Return the pricing row for ``model`` (or ``None`` if not in the table)."""
