"""Canonical model-id constants — Phase 46.

Every place in the codebase that refers to a vendor model id by string
literal should import from here instead. Centralising the strings stops
``"gpt-5.4-mini"`` from getting misspelled in one file (a silent
billing/quality regression) and makes the bump path obvious — change
the constant, every callsite picks it up.

The IDs match the entries in ``data/pricing.yaml`` and the routing
table in :mod:`lite_horse.providers`.
"""
from __future__ import annotations

# OpenAI families.
MODEL_GPT_5_4 = "gpt-5.4"
MODEL_GPT_5_4_MINI = "gpt-5.4-mini"
MODEL_GPT_5_2 = "gpt-5.2"

# OpenAI embedding model. ``text-embedding-3-small`` is 1536-dim native;
# see :data:`lite_horse.constants.EMBED_DIM`.
MODEL_EMBEDDING_3_SMALL = "text-embedding-3-small"

# Anthropic families. Opus 4.7 is the v0.5 premium default; Sonnet/Haiku
# 4.x stay available via :func:`provider_for_model`.
MODEL_CLAUDE_OPUS_4_7 = "claude-opus-4-7"
MODEL_CLAUDE_SONNET_4_6 = "claude-sonnet-4-6"
MODEL_CLAUDE_HAIKU_4_5 = "claude-haiku-4-5"


__all__ = [
    "MODEL_CLAUDE_HAIKU_4_5",
    "MODEL_CLAUDE_OPUS_4_7",
    "MODEL_CLAUDE_SONNET_4_6",
    "MODEL_EMBEDDING_3_SMALL",
    "MODEL_GPT_5_2",
    "MODEL_GPT_5_4",
    "MODEL_GPT_5_4_MINI",
]
