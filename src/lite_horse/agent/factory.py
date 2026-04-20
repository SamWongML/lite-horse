"""Agent factory for lite-horse.

Wires together the dynamic instructions, model settings, tool bundle, and the
composite :class:`LiteHorseHooks` (budget + evolution). Used by the CLI,
gateway, and cron entrypoints so everyone talks to the same agent shape.
"""
from __future__ import annotations

from typing import Any

from agents import (
    Agent,
    AgentHooks,
    ModelSettings,
    RunContextWrapper,
    Tool,
    WebSearchTool,
)
from openai.types.shared import Reasoning

from lite_horse.agent.budget import BudgetHook
from lite_horse.agent.evolution import EvolutionHook
from lite_horse.agent.instructions import make_instructions
from lite_horse.config import Config, load_config
from lite_horse.memory.tool import memory_tool
from lite_horse.sessions.search_tool import session_search
from lite_horse.skills.manage_tool import skill_manage
from lite_horse.skills.view_tool import skill_view


class LiteHorseHooks(AgentHooks[Any]):
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


def build_agent(*, name: str = "lite-horse", config: Config | None = None) -> Agent[Any]:
    """Construct the main user-facing agent.

    ``config`` can be passed in by tests to skip the on-disk load.
    """
    cfg = config or load_config()
    tools: list[Tool] = [memory_tool, session_search, skill_manage, skill_view]
    if cfg.tools.web_search:
        tools.append(WebSearchTool())
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
        tools=tools,
        hooks=LiteHorseHooks(max_turns=cfg.agent.max_turns, model=cfg.model),
    )
