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

# Skills auto-creation heuristic
SKILL_CREATION_MIN_TOOL_CALLS = 5

SCHEMA_VERSION = 1
