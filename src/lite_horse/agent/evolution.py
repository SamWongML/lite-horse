"""Post-run skill-creation hook: a side-agent reviews the trajectory and
optionally materialises a new skill.

Attached to the *main* user-facing agent via ``agent.hooks`` so it does not
recurse into the distiller side-agent. Failures are swallowed — evolution must
never break the user's run.
"""
from __future__ import annotations

import json
from typing import Any

from agents import Agent, AgentHooks, RunContextWrapper, Runner, Tool

from lite_horse.constants import SKILL_CREATION_MIN_TOOL_CALLS
from lite_horse.skills.manage_tool import skill_manage

_DISTILLER_INSTRUCTIONS = (
    "You inspect a completed agent trajectory and decide whether the work "
    "warrants a reusable Skill (procedural memory). A skill is worth writing when:\n"
    "- The task was non-trivial (>= 5 tool calls)\n"
    "- The approach is generalizable, not one-off\n"
    "- A future agent encountering a similar task would benefit\n\n"
    "If yes, call the `skill_manage` tool with action='create' and a complete "
    "SKILL.md. The SKILL.md must have YAML frontmatter:\n"
    "---\nname: short-kebab-name\ndescription: one sentence (<140 chars)\n"
    "version: 1.0.0\n---\n\n"
    "Followed by sections: ## When to Use / ## Procedure / ## Pitfalls / ## Verification.\n\n"
    "If the task does NOT warrant a skill, just respond with 'no skill warranted' "
    "and stop. Be conservative — bad skills are worse than no skill."
)


class EvolutionHook(AgentHooks[Any]):
    """After each agent run with >= ``min_tool_calls`` tool calls, ask a
    side-agent to decide whether to materialise a skill."""

    def __init__(
        self,
        *,
        model: str = "gpt-5.4",
        min_tool_calls: int = SKILL_CREATION_MIN_TOOL_CALLS,
        distiller_max_turns: int = 4,
    ) -> None:
        self.model = model
        self.min_tool_calls = min_tool_calls
        self.distiller_max_turns = distiller_max_turns
        self._tool_call_count: int = 0
        self._user_request: str | None = None
        self._final_output: str | None = None

    async def on_start(
        self, context: RunContextWrapper[Any], agent: Agent[Any]
    ) -> None:
        del agent
        self._tool_call_count = 0
        self._final_output = None
        self._user_request = self._extract_user_request(context)

    async def on_tool_end(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        tool: Tool,
        result: str,
    ) -> None:
        del context, agent, tool, result
        self._tool_call_count += 1

    async def on_end(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        output: Any,
    ) -> None:
        del agent
        self._final_output = str(output)[:1000] if output else None
        if self._tool_call_count < self.min_tool_calls:
            return
        await self._maybe_create_skill(context)

    async def _maybe_create_skill(self, context: RunContextWrapper[Any]) -> None:
        del context
        decider = Agent(
            name="skill-distiller",
            model=self.model,
            instructions=_DISTILLER_INSTRUCTIONS,
            tools=[skill_manage],
        )
        prompt = json.dumps(
            {
                "user_request": self._user_request,
                "tool_calls_used": self._tool_call_count,
                "final_output_excerpt": self._final_output,
            }
        )
        try:
            await Runner.run(decider, prompt, max_turns=self.distiller_max_turns)
        except Exception:
            # Evolution must never break the main run.
            pass

    @staticmethod
    def _extract_user_request(context: RunContextWrapper[Any]) -> str | None:
        """Best-effort scrape of the last user message from the turn input."""
        try:
            items = getattr(context, "turn_input", None)
            if not isinstance(items, list):
                return None
            for item in reversed(items):
                if isinstance(item, dict) and item.get("role") == "user":
                    content = item.get("content")
                    if isinstance(content, str):
                        return content[:500]
                    if isinstance(content, list):
                        parts = [
                            p.get("text", "")
                            for p in content
                            if isinstance(p, dict) and p.get("type") in {"text", "input_text"}
                        ]
                        joined = "".join(parts)
                        return joined[:500] if joined else None
                elif isinstance(item, str):
                    return item[:500]
        except Exception:
            return None
        return None
