"""Iteration-budget tracking with pressure warnings injected as tool messages.

The SDK's ``max_turns`` is the hard ceiling. This hook adds a *soft* signal
inside the most recent tool-role item in ``context.turn_input``, nudging the
model to wind down before the ceiling hits. Each tier (caution / warning) is
injected at most once per run.

Every ``NUDGE_EVERY_N_TOOL_CALLS`` iterations the hook also stamps a
persistence-nudge note into the same tool item — independent of the budget
tiers. The nudge asks the model to push any durable fact it just learned
into MEMORY.md before continuing. Idempotent per N-bucket by virtue of
monotonic iteration counting.

On the WARNING-tier transition the hook also fires a :class:`Consolidator`
side-agent once per run to distill durable facts out of the trajectory into
MEMORY.md before truncation. Consolidation failures are swallowed — it must
never break the user's run (same rule as :class:`EvolutionHook`).
"""
from __future__ import annotations

import copy
from typing import Any

from agents import Agent, AgentHooks, RunContextWrapper, Tool

from lite_horse.agent.consolidator import Consolidator
from lite_horse.constants import (
    BUDGET_CAUTION_THRESHOLD,
    BUDGET_WARNING_THRESHOLD,
    DEFAULT_MAX_TURNS,
    NUDGE_EVERY_N_TOOL_CALLS,
)
from lite_horse.memory.store import MemoryFull, MemoryStore, UnsafeMemoryContent

_TIER_CAUTION = "caution"
_TIER_WARNING = "warning"


class BudgetHook(AgentHooks[Any]):
    """Tracks tool-call iterations and stamps pressure notes into turn input."""

    def __init__(
        self,
        max_turns: int = DEFAULT_MAX_TURNS,
        *,
        consolidator: Consolidator | None = None,
    ) -> None:
        self.max_turns = max_turns
        self.iteration = 0
        self._last_tier: str | None = None
        self._consolidator = consolidator
        self._consolidated = False

    async def on_start(
        self, context: RunContextWrapper[Any], agent: Agent[Any]
    ) -> None:
        del context, agent
        self.iteration = 0
        self._last_tier = None
        self._consolidated = False

    async def on_tool_end(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        tool: Tool,
        result: str,
    ) -> None:
        del agent, tool, result
        self.iteration += 1
        self._maybe_emit_nudge(context)
        tier = self._tier_for(self.iteration / max(1, self.max_turns))
        if tier is None or tier == self._last_tier:
            return
        if self._append_note(context, self._note(tier)):
            self._last_tier = tier
            if tier == _TIER_WARNING and not self._consolidated:
                self._consolidated = True
                await self._consolidate(context)

    def _maybe_emit_nudge(self, context: RunContextWrapper[Any]) -> None:
        """Append a persistence nudge every N iterations.

        Monotonic iteration counting gives us idempotency for free — each
        multiple of N is hit exactly once per run.
        """
        if NUDGE_EVERY_N_TOOL_CALLS <= 0:
            return
        if self.iteration <= 0:
            return
        if self.iteration % NUDGE_EVERY_N_TOOL_CALLS != 0:
            return
        self._append_note(context, self._nudge_note())

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

    def _nudge_note(self) -> str:
        return (
            f"[NUDGE: You have done {self.iteration} tool calls. If you learned "
            "a durable fact — user preference, environmental constant, lesson "
            "— consider `memory(action='add', ...)` before continuing.]"
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

    async def _consolidate(self, context: RunContextWrapper[Any]) -> None:
        """Run the distiller once; write surviving entries straight to MEMORY.md.

        Every failure path is swallowed — consolidation is best-effort and
        must not break the user's run.
        """
        if self._consolidator is None:
            return
        try:
            items = getattr(context, "turn_input", None)
            snapshot = copy.deepcopy(items) if isinstance(items, list) else []
            entries = await self._consolidator.run(turn_input=snapshot)
        except Exception:
            return
        if not entries:
            return
        try:
            store = MemoryStore.for_memory()
        except Exception:
            return
        for entry in entries:
            try:
                store.add(entry)
            except (MemoryFull, UnsafeMemoryContent, ValueError):
                continue
            except Exception:
                return
