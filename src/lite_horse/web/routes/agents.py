"""``/v1/users/me/agents/*`` — multi-agent CRUD per user.

Each user can own one or more agents (``coder``, ``shopper``, …); every
new user gets a default agent on first sight (auto-created lazily by
:meth:`AgentRepo.ensure_default`). One agent is always marked
``is_default=true`` and pointed at by ``users.default_agent_id`` so the
turn engine can resolve it without a body parameter.

Soft-delete: ``DELETE`` flips ``archived_at`` rather than dropping rows.
The default agent cannot be archived; clients must promote another agent
first via ``POST .../{id}:default``.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.models.agent import Agent
from lite_horse.repositories.agent_repo import AgentRepo
from lite_horse.web.deps import get_db_session
from lite_horse.web.errors import ErrorKind, http_error
from lite_horse.web.schemas import AgentCreateIn, AgentOut, AgentUpdateIn

router = APIRouter(prefix="/v1/users/me/agents", tags=["agents"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def _agent_to_out(a: Agent) -> AgentOut:
    return AgentOut(
        id=str(a.id),
        slug=a.slug,
        name=a.name,
        persona=a.persona,
        default_model=a.default_model,
        permission_mode=a.permission_mode,  # type: ignore[arg-type]
        enabled_tools=list(a.enabled_tools or []),
        rate_limit_per_min=a.rate_limit_per_min,
        cost_budget_usd_micro=a.cost_budget_usd_micro,
        is_default=a.is_default,
        archived_at=a.archived_at,
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


@router.get("", response_model=list[AgentOut])
async def list_agents(session: DbSession) -> list[AgentOut]:
    repo = AgentRepo(session)
    # Lazy backfill: a user provisioned before the migration ran (or in
    # tests that bootstrap users directly) might lack a default agent.
    await repo.ensure_default()
    rows = await repo.list_for_user()
    return [_agent_to_out(r) for r in rows]


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreateIn, session: DbSession) -> AgentOut:
    repo = AgentRepo(session)
    # First write for a brand-new user creates the default implicitly so
    # the new agent isn't stranded as the only one but not_default.
    await repo.ensure_default()
    try:
        row = await repo.create(
            slug=body.slug,
            name=body.name,
            persona=body.persona,
            default_model=body.default_model,
            permission_mode=body.permission_mode,
            enabled_tools=body.enabled_tools,
            rate_limit_per_min=body.rate_limit_per_min,
            cost_budget_usd_micro=body.cost_budget_usd_micro,
        )
    except IntegrityError as exc:
        raise http_error(
            ErrorKind.CONFLICT, f"agent {body.slug!r} already exists"
        ) from exc
    return _agent_to_out(row)


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, session: DbSession) -> AgentOut:
    row = await AgentRepo(session).get(agent_id)
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"agent {agent_id!r} not found")
    return _agent_to_out(row)


@router.put("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str, body: AgentUpdateIn, session: DbSession
) -> AgentOut:
    try:
        row = await AgentRepo(session).update(
            agent_id,
            name=body.name,
            persona=body.persona,
            default_model=body.default_model,
            clear_default_model=body.clear_default_model,
            permission_mode=body.permission_mode,
            enabled_tools=body.enabled_tools,
            rate_limit_per_min=body.rate_limit_per_min,
            clear_rate_limit=body.clear_rate_limit,
            cost_budget_usd_micro=body.cost_budget_usd_micro,
            clear_cost_budget=body.clear_cost_budget,
        )
    except ValueError as exc:
        raise http_error(ErrorKind.CONFLICT, str(exc)) from exc
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"agent {agent_id!r} not found")
    return _agent_to_out(row)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_agent(agent_id: str, session: DbSession) -> None:
    try:
        ok = await AgentRepo(session).archive(agent_id)
    except ValueError as exc:
        raise http_error(ErrorKind.CONFLICT, str(exc)) from exc
    if not ok:
        raise http_error(ErrorKind.NOT_FOUND, f"agent {agent_id!r} not found")


@router.post("/{agent_id}:default", response_model=AgentOut)
async def set_default_agent(agent_id: str, session: DbSession) -> AgentOut:
    row = await AgentRepo(session).set_default(agent_id)
    if row is None:
        raise http_error(
            ErrorKind.NOT_FOUND,
            f"agent {agent_id!r} not found or archived",
        )
    return _agent_to_out(row)
