"""Per-user streaming turn engine — Phase 33 cloud path.

The v0.3 ``lite_horse.api.run_turn_streaming`` builds a process-global
``Agent`` once and serves every request through it. Under multi-tenant
load that ignores per-user effective config, MCP entries, BYO keys,
and ``default_model`` / ``permission_mode`` settings — a user's writes
to ``skill_manage`` etc. would never reach the next turn's prompt.

This module is the cloud equivalent: each turn opens a tenant-scoped
``db_session(user_id)``, resolves the per-user effective config, memory
documents, BYO keys, MCP servers, and permission policy, then builds a
fresh agent via :func:`lite_horse.agent.factory.build_agent_for_user`.
The agent is run via ``Runner.run_streamed`` and the events are
translated to the same :mod:`lite_horse.api` ``StreamEvent`` shapes the
SSE driver already understands.

Session message storage still goes through the v0.3 ``LocalSessionRepo``
for now — replacing that with a Postgres-backed ``SDKSession`` is a
separate Phase 33 deliverable kept out of this PR's scope.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator

from agents import Runner

from lite_horse.agent.factory import (
    build_agent_for_user,
    build_cloud_tenant_context,
    resolve_provider,
)
from lite_horse.agent.mcp_pool import McpPool
from lite_horse.api import (
    RunResult,
    StreamDone,
    StreamEvent,
    _ensure_ready,
    _process_stream_event,
    _StreamCounters,
)
from lite_horse.core.permission import PermissionPolicy, get_policy
from lite_horse.repositories.agent_repo import AgentRepo
from lite_horse.repositories.byo_repo import ByoKeyStore
from lite_horse.repositories.memory_repo import MemoryRepo
from lite_horse.repositories.user_settings_repo import UserSettingsRepo
from lite_horse.sessions.sdk_session import SDKSession
from lite_horse.storage.db import db_session
from lite_horse.storage.kms import Kms
from lite_horse.storage.redis_client import Redis
from lite_horse.web.effective_cache import get_or_compute_effective_config
from lite_horse.web.turns import TurnRequest

log = logging.getLogger(__name__)


_PROVIDER_ENV_FALLBACK = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


async def run_turn_streaming_for_user(  # noqa: PLR0912, PLR0915
    req: TurnRequest,
    *,
    mcp_pool: McpPool | None,
    kms: Kms | None,
    redis: Redis | None,
) -> AsyncIterator[StreamEvent]:
    """Run one cloud turn through a per-user agent.

    Mirrors the public surface of :func:`lite_horse.api.run_turn_streaming`
    (yields :class:`StreamDelta` / :class:`StreamToolCall` /
    :class:`StreamToolOutput` / :class:`StreamDone`) so the SSE
    translator in :mod:`lite_horse.web.turns` can drive it without
    changes.
    """
    db, _ignored_global_agent, cfg = await _ensure_ready()

    # Resolve which of the user's agents owns this turn. The agent_id GUC
    # is then set for every subsequent ``db_session`` open so RLS narrows
    # reads to that agent's slice. Resolution order: request body →
    # users.default_agent_id (auto-created if missing) → escalate.
    async with db_session(req.user_id) as bootstrap:
        agent_repo = AgentRepo(bootstrap)
        if req.agent_id is not None:
            agent_row = await agent_repo.get(req.agent_id)
            if agent_row is None or agent_row.archived_at is not None:
                raise ValueError(f"agent {req.agent_id!r} not found")
        else:
            agent_row = await agent_repo.ensure_default()
    resolved_agent_id = str(agent_row.id)

    # Tenant-scoped reads — close the session before running the agent so
    # the request connection isn't pinned for the duration of the turn.
    async with db_session(req.user_id, resolved_agent_id) as session:
        eff = await get_or_compute_effective_config(
            session, redis=redis, user_id=req.user_id
        )
        memory_repo = MemoryRepo(session)
        memory_text = await memory_repo.get("memory.md")
        user_md_text = await memory_repo.get("user.md")
        user_settings = await UserSettingsRepo(session).get()

        # Per-agent overrides shadow per-user defaults: persona / model /
        # permission_mode are agent-scoped.
        agent_default_model = agent_row.default_model
        agent_permission_mode = agent_row.permission_mode

        chosen_model = (
            req.model or agent_default_model or user_settings.default_model
        )
        provider, model_name = resolve_provider(
            default_model=chosen_model, fallback_model=cfg.model
        )

        api_key: str | None = None
        github_token: str | None = None
        if kms is not None:
            byo = ByoKeyStore(session, kms)
            api_key = await byo.get_key(provider.name)
            github_token = await byo.get_key("github")

    if not api_key:
        env_name = _PROVIDER_ENV_FALLBACK.get(provider.name)
        if env_name is not None:
            api_key = os.environ.get(env_name, "") or None
    if not api_key:
        log.warning(
            "no API key available for provider %s (BYO unset, %s missing)",
            provider.name,
            _PROVIDER_ENV_FALLBACK.get(provider.name, "<env>"),
        )
        api_key = ""

    if mcp_pool is not None:
        mcp_servers = await mcp_pool.acquire(user_id=req.user_id, eff=eff)
    else:
        mcp_servers = []

    policy = get_policy(req.session_key)
    if policy is None:
        # Per-agent permission_mode shadows users.permission_mode.
        effective_mode = agent_permission_mode or user_settings.permission_mode
        if effective_mode == "ro":
            policy = PermissionPolicy(mode="ro")

    agent = build_agent_for_user(
        eff=eff,
        memory_text=memory_text,
        user_md_text=user_md_text,
        user_id=req.user_id,
        api_key=api_key,
        mcp_servers=mcp_servers,
        permission_policy=policy,
        model_override=chosen_model,
        github_token=github_token,
        config=cfg,
    )

    sdk_session = SDKSession(
        req.session_key,
        db,
        source="web",
        user_id=req.user_id,
        model=model_name,
    )
    tenant_ctx = build_cloud_tenant_context(
        user_id=req.user_id, agent_id=resolved_agent_id, eff=eff
    )
    streaming = Runner.run_streamed(
        agent,
        req.text,
        session=sdk_session,  # type: ignore[arg-type]
        max_turns=cfg.agent.max_turns,
        context=tenant_ctx,
    )

    counters = _StreamCounters()
    async for event in streaming.stream_events():
        async for emitted in _process_stream_event(event, counters):
            yield emitted

    final_text = ""
    try:
        final_text = str(streaming.final_output)
    except Exception:
        log.exception("run_turn_streaming_for_user: failed to read final_output")
    yield StreamDone(
        result=RunResult(
            final_output=final_text,
            session_key=req.session_key,
            turn_count=(
                len(streaming.raw_responses)
                if hasattr(streaming, "raw_responses")
                else 0
            ),
            tool_calls=counters.tool_calls,
            input_tokens=counters.input_tokens if counters.saw_usage else None,
            output_tokens=counters.output_tokens if counters.saw_usage else None,
            total_tokens=counters.total_tokens if counters.saw_usage else None,
        )
    )
