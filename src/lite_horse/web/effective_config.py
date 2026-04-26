"""Per-user effective-config resolver.

Folds the bundled (image-static), official (admin-injected DB), and user
(per-tenant DB) tiers into one :class:`EffectiveConfig` document. The
resolution rules live on each repository's ``list_effective`` so they can
be tested in isolation; this module's only job is to fan out the
opt-out lookup, run the four ``list_effective`` calls, and pack the
result.

Redis caching (60 s TTL, pub/sub invalidation) lands in Phase 33c when
the HTTP route surface is wired up — the route is the natural cache
boundary because that's where ``user_id`` enters the system.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.effective import EffectiveConfig
from lite_horse.repositories.command_repo import CommandRepo
from lite_horse.repositories.instruction_repo import InstructionRepo
from lite_horse.repositories.mcp_repo import McpRepo
from lite_horse.repositories.opt_out_repo import OptOutRepo
from lite_horse.repositories.skill_repo import SkillRepo


async def compute_effective_config(session: AsyncSession) -> EffectiveConfig:
    """Build the resolved config for whoever owns the current GUC.

    The session must already be inside ``db_session(user_id)`` — the
    repos read ``app.user_id`` from the GUC, never from a kwarg, so
    there's no way to accidentally compute config for the wrong tenant.
    """
    opt_outs = await OptOutRepo(session).list()
    by_entity: dict[str, set[str]] = {}
    for entity, slug in opt_outs:
        by_entity.setdefault(entity, set()).add(slug)

    skills = await SkillRepo(session).list_effective(by_entity.get("skill", set()))
    instructions = await InstructionRepo(session).list_effective(
        by_entity.get("instruction", set())
    )
    commands = await CommandRepo(session).list_effective(
        by_entity.get("command", set())
    )
    mcp_servers = await McpRepo(session).list_effective(
        by_entity.get("mcp_server", set())
    )

    return EffectiveConfig.build(
        skills=skills,
        instructions=instructions,
        commands=commands,
        mcp_servers=mcp_servers,
    )
