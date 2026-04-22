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

# Conditional skill activation (Phase 21). At most this many skills render in
# the prompt's AVAILABLE SKILLS index per turn; ones without `activate_when`
# frontmatter are always eligible and score as defaults.
ACTIVATION_TOP_K = 8

# Phase 24 — offline evolve. SKILL.md must stay small so retrieval-time cost is
# bounded; the reflector rejects candidates larger than this.
SKILL_MAX_BYTES = 15_360

SCHEMA_VERSION = 2
