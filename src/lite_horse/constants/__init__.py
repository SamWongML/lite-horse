"""Global constants and path helpers for lite-horse."""
from __future__ import annotations

import os
from pathlib import Path


def litehorse_home() -> Path:
    """Return the state directory, honoring LITEHORSE_HOME override."""
    return Path(os.environ.get("LITEHORSE_HOME", Path.home() / ".litehorse"))


# Prompt-block char limits — DO NOT change without updating prompt expectations.
MEMORY_CHAR_LIMIT = 2200        # ~800 tokens
USER_PROFILE_CHAR_LIMIT = 1375  # ~500 tokens
ENTRY_DELIMITER = "\n§\n"       # § (section sign)

DEFAULT_MAX_TURNS = 90
BUDGET_CAUTION_THRESHOLD = 0.70
BUDGET_WARNING_THRESHOLD = 0.90

# Every N tool calls, BudgetHook injects a one-shot persistence nudge so the
# model is reminded to push durable facts to MEMORY.md mid-run. Independent
# of the budget tiers: CAUTION/WARNING signal "wind down", nudges signal
# "remember".
NUDGE_EVERY_N_TOOL_CALLS = 10

# Skills auto-creation heuristic
SKILL_CREATION_MIN_TOOL_CALLS = 5

# Conditional skill activation. At most this many skills render in
# the prompt's AVAILABLE SKILLS index per turn; ones without `activate_when`
# frontmatter are always eligible and score as defaults.
ACTIVATION_TOP_K = 8

# Offline evolve. SKILL.md must stay small so retrieval-time cost is
# bounded; the reflector rejects candidates larger than this.
SKILL_MAX_BYTES = 15_360

SCHEMA_VERSION = 2

# Embedding dimension. Memory chunks store ``vector(1536)``.
# OpenAI ``text-embedding-3-small`` is 1536-dim natively; Voyage's 1024
# dims are right-padded to fit this fixed shape.
EMBED_DIM = 1536

# Curator + outcome classifier thresholds.
# A skill must have at least this many recorded outcomes before the
# outcome-classifier's signal can drive ``EvolutionHook`` refinement;
# protects against false-failure noise on rarely-used skills.
CURATOR_REFINE_MIN_OUTCOMES = 5
# Days of idle time (no ``last_used_at`` movement) before a skill flips
# from ``active`` → ``stale``.
CURATOR_STALE_AFTER_DAYS = 30
# Idle days + zero successes flip a skill from ``stale`` → ``archived``.
CURATOR_ARCHIVE_AFTER_DAYS = 90
# Cosine similarity between two skill bodies above which the curator
# proposes a consolidation. 0.85 was the Hermes-side default and matches
# the regression test in ``tests/agent/test_curator_consolidate.py``.
CURATOR_CONSOLIDATE_COSINE = 0.85

# Skill promotion thresholds. The daily promotion tick groups
# user-scope skills by ``frontmatter.name`` and surfaces ones that have
# spread across enough independent users, been used enough times, and
# have a high enough success rate.
PROMOTION_MIN_UNIQUE_USERS = 3
PROMOTION_MIN_USE_COUNT = 20
PROMOTION_MIN_SUCCESS_RATE = 0.8

# GEPA cost gate. Aborts a run before any model call when the estimated
# cost exceeds this ceiling (USD). Opt-in via SKILL.md frontmatter
# ``gepa: true``.
GEPA_COST_GATE_USD = 20.0
GEPA_DEFAULT_POPULATION_SIZE = 8
GEPA_DEFAULT_GENERATIONS = 3
GEPA_DIVERSITY_COSINE = 0.95
GEPA_MIN_TRAJECTORIES = 10
GEPA_MAX_TRAJECTORIES = 50
