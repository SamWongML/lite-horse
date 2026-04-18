"""Agent factory for hermes-lite.

Wires together the dynamic instructions, model settings, tool bundle, and the
composite :class:`HermesLiteHooks` (budget + evolution). Used by the CLI,
gateway, and cron entrypoints so everyone talks to the same agent shape.
"""
from __future__ import annotations

from typing import Any

from agents import Agent, AgentHooks, ModelSettings, RunContextWrapper, Tool
from openai.types.shared import Reasoning

from hermes_lite.agent.budget import BudgetHook
from hermes_lite.agent.evolution import EvolutionHook
from hermes_lite.agent.instructions import make_instructions
from hermes_lite.config import Config, load_config
from hermes_lite.memory.tool import memory_tool
from hermes_lite.sessions.search_tool import session_search
from hermes_lite.skills.manage_tool import skill_manage


class HermesLiteHooks(AgentHooks[Any]):
    """Composite hook forwarding lifecycle events to budget + evolution.

    The SDK's ``Agent.hooks`` slot accepts a single ``AgentHooks`` instance, so
    we own both sub-hooks internally and dispatch to each.
    """

    def __init__(self, *, max_turns: int, model: str) -> None:
        self._budget = BudgetHook(max_turns=max_turns)
        self._evo = EvolutionHook(model=model)

    async def on_start(
        self, context: RunContextWrapper[Any], agent: Agent[Any]
    ) -> None:
        await self._budget.on_start(context, agent)
        await self._evo.on_start(context, agent)

    async def on_tool_end(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        tool: Tool,
        result: str,
    ) -> None:
        await self._budget.on_tool_end(context, agent, tool, result)
        await self._evo.on_tool_end(context, agent, tool, result)

    async def on_end(
        self, context: RunContextWrapper[Any], agent: Agent[Any], output: Any
    ) -> None:
        await self._evo.on_end(context, agent, output)


def build_agent(*, name: str = "hermes-lite", config: Config | None = None) -> Agent[Any]:
    """Construct the main user-facing agent.

    ``config`` can be passed in by tests to skip the on-disk load.
    """
    cfg = config or load_config()
    return Agent(
        name=name,
        model=cfg.model,
        instructions=make_instructions(),
        model_settings=ModelSettings(
            reasoning=Reasoning(effort=cfg.model_settings.reasoning_effort),
            parallel_tool_calls=cfg.model_settings.parallel_tool_calls,
            store=True,
            prompt_cache_retention="24h",
        ),
        tools=[memory_tool, session_search, skill_manage],
        hooks=HermesLiteHooks(max_turns=cfg.agent.max_turns, model=cfg.model),
    )
