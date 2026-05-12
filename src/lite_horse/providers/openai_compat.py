"""OpenAI-compatible provider — passthrough to any OpenAI-API-shaped server.

Covers self-hosted Azure OpenAI passthrough, LiteLLM proxies (which expose
the OpenAI chat-completions wire shape including the Responses-compatible
surface), Ollama's OpenAI endpoint, vLLM, and similar.

Routing is explicit via a ``oai/`` prefix so it can't accidentally shadow
the native OpenAI or Anthropic matchers — e.g. ``oai/azure-gpt-4o-mini``
or ``oai/llama-3.1-70b``. The prefix is stripped before the model name
is sent upstream; the upstream sees its own native naming (Azure
deployment id, LiteLLM-registered name, etc.).

``base_url`` comes from the call site if provided, else from the
``OPENAI_COMPAT_BASE_URL`` env var. This is the only required piece of
config — without it the provider has nothing to talk to and raises at
``build_model`` time. The API key falls back to ``OPENAI_COMPAT_API_KEY``
(many local servers accept any non-empty string).
"""
from __future__ import annotations

import os

from agents.models.interface import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from lite_horse.providers.base import ProviderName
from lite_horse.providers.pricing import ModelPricing, get_pricing_table

COMPAT_PREFIX = "oai/"
COMPAT_BASE_URL_ENV = "OPENAI_COMPAT_BASE_URL"


def _strip_prefix(model: str) -> str:
    return model[len(COMPAT_PREFIX):] if model.startswith(COMPAT_PREFIX) else model


class OpenAICompatibleProvider:
    """Passthrough provider for any OpenAI-API-shaped endpoint.

    Routes models named ``oai/<upstream-name>`` to a configurable
    ``base_url``. The ``oai/`` prefix is stripped before the request is
    sent so the upstream receives its own native model identifier.
    """

    name: ProviderName = "openai_compat"

    def matches(self, model: str) -> bool:
        return model.startswith(COMPAT_PREFIX)

    def build_model(
        self, model: str, api_key: str, *, base_url: str | None = None
    ) -> Model:
        upstream_model = _strip_prefix(model)
        resolved_base_url = base_url or os.environ.get(COMPAT_BASE_URL_ENV)
        if not resolved_base_url:
            raise ValueError(
                f"openai-compat provider needs a base_url; set "
                f"{COMPAT_BASE_URL_ENV} or pass base_url explicitly"
            )
        client = AsyncOpenAI(api_key=api_key, base_url=resolved_base_url)
        return OpenAIChatCompletionsModel(
            model=upstream_model, openai_client=client
        )

    def pricing(self, model: str) -> ModelPricing | None:
        return get_pricing_table().get(_strip_prefix(model))
