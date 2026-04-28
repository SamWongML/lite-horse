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
from agents.mcp import MCPServer, MCPServerStreamableHttp
from agents.models.interface import Model
from openai.types.shared import Reasoning

from lite_horse.agent.budget import BudgetHook
from lite_horse.agent.consolidator import Consolidator
from lite_horse.agent.evolution import EvolutionHook
from lite_horse.agent.instructions import make_instructions, make_instructions_for_user
from lite_horse.config import Config, load_config
from lite_horse.core.permission import PermissionPolicy, filter_tools
from lite_horse.cron.manage_tool import cron_manage
from lite_horse.effective import EffectiveConfig
from lite_horse.memory.tool import memory_tool
from lite_horse.providers import ModelProvider, provider_for_model
from lite_horse.sessions.search_tool import session_search
from lite_horse.skills.manage_tool import skill_manage
from lite_horse.skills.view_tool import skill_view
from lite_horse.storage.kms import Kms
from lite_horse.tools.github import build_github_tools


class LiteHorseHooks(AgentHooks[Any]):
    """Composite hook forwarding lifecycle events to budget + evolution.

    The SDK's ``Agent.hooks`` slot accepts a single ``AgentHooks`` instance, so
    we own both sub-hooks internally and dispatch to each.
    """

    def __init__(self, *, max_turns: int, model: str) -> None:
        self._budget = BudgetHook(
            max_turns=max_turns,
            consolidator=Consolidator(model=model),
        )
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


def build_mcp_servers(config: Config) -> list[MCPServer]:
    """Instantiate one :class:`MCPServerStreamableHttp` per configured entry.

    Caller owns connect/cleanup lifecycle.
    """
    return [
        MCPServerStreamableHttp(
            name=spec.name,
            params={"url": spec.url},
            cache_tools_list=spec.cache_tools_list,
        )
        for spec in config.mcp_servers
    ]


def build_agent(
    *,
    name: str = "lite-horse",
    config: Config | None = None,
    mcp_servers: list[MCPServer] | None = None,
    permission_policy: PermissionPolicy | None = None,
) -> Agent[Any]:
    """Construct the main user-facing agent.

    ``config`` can be passed in by tests to skip the on-disk load.
    ``mcp_servers`` is an already-constructed list whose lifecycle the caller
    manages; when omitted, none are attached.
    ``permission_policy`` (when provided) filters the tool bundle at build
    time: under ``ro`` mode write tools are removed so the model cannot
    invoke them at all.
    """
    cfg = config or load_config()
    tools: list[Tool] = [
        memory_tool,
        session_search,
        skill_manage,
        skill_view,
        cron_manage,
    ]
    if cfg.tools.web_search:
        tools.append(WebSearchTool())
    if permission_policy is not None:
        tools = filter_tools(tools, permission_policy)
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
        mcp_servers=mcp_servers or [],
        hooks=LiteHorseHooks(max_turns=cfg.agent.max_turns, model=cfg.model),
    )


# ---------- v0.4 cloud path ----------


async def build_mcp_servers_for_user(
    eff: EffectiveConfig, kms: Kms
) -> list[MCPServer]:
    """Materialise resolved MCP entries into SDK ``MCPServer`` instances.

    Decrypts each entry's auth value through ``kms`` with the
    encryption-context that the row was written under (``user_id`` for
    user-scope, ``"official"`` for official-scope). Disabled entries are
    already filtered upstream by ``McpRepo.list_effective``.
    """
    servers: list[MCPServer] = []
    for entry in eff.mcp_servers:
        params: dict[str, Any] = {"url": entry.url}
        if entry.auth_value_ct is not None and entry.auth_header:
            owner = entry.user_id or "official"
            plaintext = await kms.decrypt(
                entry.auth_value_ct, {"user_id": owner}
            )
            params["headers"] = {
                entry.auth_header: plaintext.decode("utf-8")
            }
        servers.append(
            MCPServerStreamableHttp(
                name=entry.slug,
                params=params,  # type: ignore[arg-type]
                cache_tools_list=entry.cache_tools_list,
            )
        )
    return servers


def resolve_provider(
    *, default_model: str | None, fallback_model: str
) -> tuple[ModelProvider, str]:
    """Pick the provider + model name for one turn.

    ``default_model`` comes from ``users.default_model``; if unset we fall
    back to the runtime ``Config.model``. The provider is the first
    registered match for that name.
    """
    model = default_model or fallback_model
    return provider_for_model(model), model


def build_agent_for_user(
    *,
    eff: EffectiveConfig,
    memory_text: str,
    user_md_text: str,
    user_id: str,
    api_key: str,
    name: str = "lite-horse",
    config: Config | None = None,
    mcp_servers: list[MCPServer] | None = None,
    permission_policy: PermissionPolicy | None = None,
    model_override: str | None = None,
    github_token: str | None = None,
) -> Agent[Any]:
    """Cloud-path agent factory. Reads everything from the resolver.

    Caller is responsible for:
    - resolving ``eff`` via ``compute_effective_config``,
    - fetching ``memory_text`` and ``user_md_text`` from ``MemoryRepo``,
    - decrypting the BYO provider key (``api_key``) for the chosen model,
    - constructing ``mcp_servers`` via :func:`build_mcp_servers_for_user`
      and managing their connect/cleanup lifecycle.

    ``model_override`` lets the caller plumb ``users.default_model``
    through without re-reading ``Config``. ``github_token`` (when set)
    enables the bundled GitHub tool surface.
    """
    cfg = config or load_config()
    model_name = model_override or cfg.model
    provider, model_name = resolve_provider(
        default_model=model_override, fallback_model=cfg.model
    )
    sdk_model: Model = provider.build_model(model_name, api_key)
    tools: list[Tool] = [
        memory_tool,
        session_search,
        skill_manage,
        skill_view,
        cron_manage,
    ]
    if cfg.tools.web_search:
        tools.append(WebSearchTool())
    if github_token:
        tools.extend(build_github_tools(token_provider=lambda: github_token))
    if permission_policy is not None:
        tools = filter_tools(tools, permission_policy)
    return Agent(
        name=name,
        model=sdk_model,
        instructions=make_instructions_for_user(
            eff, memory_text=memory_text, user_md_text=user_md_text
        ),
        model_settings=ModelSettings(
            reasoning=Reasoning(effort=cfg.model_settings.reasoning_effort),
            parallel_tool_calls=cfg.model_settings.parallel_tool_calls,
            store=True,
            prompt_cache_retention="24h",
        ),
        tools=tools,
        mcp_servers=mcp_servers or [],
        hooks=LiteHorseHooks(max_turns=cfg.agent.max_turns, model=model_name),
    )
