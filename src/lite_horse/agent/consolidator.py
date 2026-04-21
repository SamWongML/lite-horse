"""Compression-as-consolidation side-agent (Phase 18).

At the WARNING budget tier, :class:`BudgetHook` invokes this distiller to
identify durable facts in the current trajectory and return them as candidate
MEMORY.md entries. The caller writes them directly via
:class:`MemoryStore.add` — bypassing ``memory_tool`` keeps the side-agent
read-only and routes content through the same validation path.
"""
from __future__ import annotations

import json
from typing import Any

from agents import Agent, Runner

CONSOLIDATOR_MAX_ENTRIES = 5
CONSOLIDATOR_MAX_ENTRY_CHARS = 200

_CONSOLIDATOR_INSTRUCTIONS = (
    "You inspect an in-flight agent trajectory that is about to be truncated. "
    "Identify up to 5 durable facts worth surviving: a user preference, a "
    "project convention, an environmental constant, or a lesson the next run "
    "would need. Skip transient details, unresolved plans, and anything tied "
    "to this specific task.\n\n"
    "Each fact must be <= 200 characters and stand on its own without "
    "context. Return a JSON array of strings and nothing else, e.g. "
    '["user prefers terse answers", "project uses pnpm, not npm"]. '
    "If nothing is worth saving, return []."
)


class Consolidator:
    """Side-agent that distills ``turn_input`` into memory-entry candidates."""

    def __init__(self, *, model: str, max_turns: int = 3) -> None:
        self.model = model
        self.max_turns = max_turns

    async def run(self, *, turn_input: list[dict[str, Any]]) -> list[str]:
        """Return validated entries. Never raises — caller swallows nothing."""
        agent = Agent(
            name="consolidator",
            model=self.model,
            instructions=_CONSOLIDATOR_INSTRUCTIONS,
        )
        prompt = json.dumps({"trajectory": turn_input}, default=str)
        result = await Runner.run(agent, prompt, max_turns=self.max_turns)
        output = getattr(result, "final_output", None)
        if not output:
            return []
        return _parse_entries(str(output))


def _parse_entries(output: str) -> list[str]:
    """Extract valid <=200-char string entries from the side-agent response.

    Tolerant of stray whitespace or a fenced code block around the JSON.
    """
    stripped = output.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").lstrip("json").strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    entries: list[str] = []
    for item in data[:CONSOLIDATOR_MAX_ENTRIES]:
        if not isinstance(item, str):
            continue
        trimmed = item.strip()
        if trimmed and len(trimmed) <= CONSOLIDATOR_MAX_ENTRY_CHARS:
            entries.append(trimmed)
    return entries
