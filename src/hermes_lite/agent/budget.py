"""Iteration-budget tracking with pressure warnings injected as tool messages.

The SDK's ``max_turns`` is the hard ceiling. This hook adds a *soft* signal
inside the most recent tool-role item in ``context.turn_input``, nudging the
model to wind down before the ceiling hits. Each tier (caution / warning) is
injected at most once per run.
"""
from __future__ import annotations

from typing import Any

from agents import Agent, AgentHooks, RunContextWrapper, Tool

from hermes_lite.constants import (
    BUDGET_CAUTION_THRESHOLD,
    BUDGET_WARNING_THRESHOLD,
    DEFAULT_MAX_TURNS,
)

_TIER_CAUTION = "caution"
_TIER_WARNING = "warning"


class BudgetHook(AgentHooks[Any]):
    """Tracks tool-call iterations and stamps pressure notes into turn input."""

    def __init__(self, max_turns: int = DEFAULT_MAX_TURNS) -> None:
        self.max_turns = max_turns
        self.iteration = 0
        self._last_tier: str | None = None

    async def on_start(
        self, context: RunContextWrapper[Any], agent: Agent[Any]
    ) -> None:
        del context, agent
        self.iteration = 0
        self._last_tier = None

    async def on_tool_end(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        tool: Tool,
        result: str,
    ) -> None:
        del agent, tool, result
        self.iteration += 1
        tier = self._tier_for(self.iteration / max(1, self.max_turns))
        if tier is None or tier == self._last_tier:
            return
        if self._append_note(context, self._note(tier)):
            self._last_tier = tier

    def _tier_for(self, ratio: float) -> str | None:
        if ratio >= BUDGET_WARNING_THRESHOLD:
            return _TIER_WARNING
        if ratio >= BUDGET_CAUTION_THRESHOLD:
            return _TIER_CAUTION
        return None

    def _note(self, tier: str) -> str:
        left = max(0, self.max_turns - self.iteration)
        if tier == _TIER_WARNING:
            return (
                f"[BUDGET WARNING: Iteration {self.iteration}/{self.max_turns}. "
                f"Only {left} iteration(s) left. Provide your final response NOW.]"
            )
        return (
            f"[BUDGET: Iteration {self.iteration}/{self.max_turns}. "
            f"{left} iterations left. Start consolidating your work.]"
        )

    @staticmethod
    def _append_note(context: RunContextWrapper[Any], note: str) -> bool:
        """Append ``note`` to the most recent tool-role item in ``turn_input``.

        Returns True on success so the caller can advance ``_last_tier`` only
        when the note actually landed.
        """
        try:
            items = getattr(context, "turn_input", None)
            if not isinstance(items, list):
                return False
            for item in reversed(items):
                if isinstance(item, dict) and item.get("role") == "tool":
                    existing = item.get("content") or ""
                    item["content"] = f"{existing}\n\n{note}" if existing else note
                    return True
        except Exception:
            return False
        return False
