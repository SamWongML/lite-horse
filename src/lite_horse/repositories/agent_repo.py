"""agents table CRUD — per-user persona / model / tool-bundle / caps.

Phase 41 introduced this table. Every user-scope row in the layered
config (skills / instructions / commands / mcp_servers / cron_jobs) and
every per-user row in user_documents / sessions / skill_proposals carries
an ``agent_id`` FK so writes are scoped to one persona at a time.

The repo stays thin: list / get / create / update / soft-delete and
``set_default``. Cross-tenant reads are intentionally absent — admin
work that needs to enumerate every agent uses
:meth:`AgentRepo.list_for_user` after switching tenants explicitly via
``db_session(user_id=...)``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, select, update

from lite_horse.models.agent import Agent
from lite_horse.models.user import User
from lite_horse.repositories.base import BaseRepo


class AgentRepo(BaseRepo):
    """``agents`` CRUD scoped to the current ``app.user_id`` GUC."""

    async def list_for_user(self, *, include_archived: bool = False) -> list[Agent]:
        user_id = UUID(await self.current_user_id())
        conditions = [Agent.user_id == user_id]
        if not include_archived:
            conditions.append(Agent.archived_at.is_(None))
        stmt = (
            select(Agent)
            .where(and_(*conditions))
            .order_by(Agent.is_default.desc(), Agent.slug)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get(self, agent_id: str | UUID) -> Agent | None:
        user_id = UUID(await self.current_user_id())
        aid = agent_id if isinstance(agent_id, UUID) else UUID(str(agent_id))
        stmt = select(Agent).where(
            and_(Agent.user_id == user_id, Agent.id == aid)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Agent | None:
        user_id = UUID(await self.current_user_id())
        stmt = select(Agent).where(
            and_(Agent.user_id == user_id, Agent.slug == slug)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_default(self) -> Agent | None:
        user_id = UUID(await self.current_user_id())
        stmt = select(Agent).where(
            and_(
                Agent.user_id == user_id,
                Agent.is_default.is_(True),
                Agent.archived_at.is_(None),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        slug: str,
        name: str,
        persona: str = "",
        default_model: str | None = None,
        permission_mode: str = "auto",
        enabled_tools: list[str] | None = None,
        rate_limit_per_min: int | None = None,
        cost_budget_usd_micro: int | None = None,
        is_default: bool = False,
    ) -> Agent:
        if permission_mode not in ("auto", "ask", "ro"):
            raise ValueError(f"invalid permission_mode: {permission_mode!r}")
        user_id = UUID(await self.current_user_id())
        row = Agent(
            id=uuid4(),
            user_id=user_id,
            slug=slug,
            name=name,
            persona=persona,
            default_model=default_model,
            permission_mode=permission_mode,
            enabled_tools=list(enabled_tools or []),
            rate_limit_per_min=rate_limit_per_min,
            cost_budget_usd_micro=cost_budget_usd_micro,
            is_default=is_default,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(
        self,
        agent_id: str | UUID,
        *,
        name: str | None = None,
        persona: str | None = None,
        default_model: str | None = None,
        clear_default_model: bool = False,
        permission_mode: str | None = None,
        enabled_tools: list[str] | None = None,
        rate_limit_per_min: int | None = None,
        clear_rate_limit: bool = False,
        cost_budget_usd_micro: int | None = None,
        clear_cost_budget: bool = False,
    ) -> Agent | None:
        if permission_mode is not None and permission_mode not in (
            "auto",
            "ask",
            "ro",
        ):
            raise ValueError(f"invalid permission_mode: {permission_mode!r}")
        user_id = UUID(await self.current_user_id())
        aid = agent_id if isinstance(agent_id, UUID) else UUID(str(agent_id))
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name
        if persona is not None:
            values["persona"] = persona
        if default_model is not None:
            values["default_model"] = default_model
        elif clear_default_model:
            values["default_model"] = None
        if permission_mode is not None:
            values["permission_mode"] = permission_mode
        if enabled_tools is not None:
            values["enabled_tools"] = list(enabled_tools)
        if rate_limit_per_min is not None:
            values["rate_limit_per_min"] = rate_limit_per_min
        elif clear_rate_limit:
            values["rate_limit_per_min"] = None
        if cost_budget_usd_micro is not None:
            values["cost_budget_usd_micro"] = cost_budget_usd_micro
        elif clear_cost_budget:
            values["cost_budget_usd_micro"] = None
        if not values:
            return await self.get(aid)
        values["updated_at"] = datetime.now(UTC)
        stmt = (
            update(Agent)
            .where(and_(Agent.user_id == user_id, Agent.id == aid))
            .values(**values)
            .returning(Agent)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def archive(self, agent_id: str | UUID) -> bool:
        """Soft-delete (set ``archived_at``). Default agent cannot be archived."""
        user_id = UUID(await self.current_user_id())
        aid = agent_id if isinstance(agent_id, UUID) else UUID(str(agent_id))
        existing = await self.get(aid)
        if existing is None:
            return False
        if existing.is_default:
            raise ValueError("cannot archive the default agent")
        when = datetime.now(UTC)
        await self.session.execute(
            update(Agent)
            .where(and_(Agent.user_id == user_id, Agent.id == aid))
            .values(archived_at=when, updated_at=when)
        )
        return True

    async def set_default(self, agent_id: str | UUID) -> Agent | None:
        """Make ``agent_id`` the user's default; clear the prior default."""
        user_id = UUID(await self.current_user_id())
        aid = agent_id if isinstance(agent_id, UUID) else UUID(str(agent_id))
        target = await self.get(aid)
        if target is None or target.archived_at is not None:
            return None
        when = datetime.now(UTC)
        # Demote any current default first to avoid the partial-unique-index
        # collision (agents_one_default_per_user).
        await self.session.execute(
            update(Agent)
            .where(
                and_(
                    Agent.user_id == user_id,
                    Agent.is_default.is_(True),
                    Agent.id != aid,
                )
            )
            .values(is_default=False, updated_at=when)
        )
        stmt = (
            update(Agent)
            .where(and_(Agent.user_id == user_id, Agent.id == aid))
            .values(is_default=True, updated_at=when)
            .returning(Agent)
        )
        updated = (await self.session.execute(stmt)).scalar_one_or_none()
        # Stamp users.default_agent_id so the turn route can resolve it
        # without re-querying ``agents``.
        await self.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(default_agent_id=aid)
        )
        return updated

    async def ensure_default(self) -> Agent:
        """Return the user's default agent, creating it if missing.

        Mirrors the migration backfill so a freshly-provisioned user
        (created by ``web/auth.py`` after the migration ran) gets a default
        agent on first request without a separate seed step.
        """
        existing = await self.get_default()
        if existing is not None:
            return existing
        agent = await self.create(
            slug="default", name="default", is_default=True
        )
        user_id = UUID(await self.current_user_id())
        await self.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(default_agent_id=agent.id)
        )
        return agent
