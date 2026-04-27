"""``/v1/admin/*`` — official-scope CRUD, versioning, audit, mcp health.

Every route here is gated by :func:`require_admin` (JWT ``role=admin``).
Successful writes:

1. Persist the change (skill / instruction / command / mcp / cron).
2. Append an ``audit_log`` row capturing actor, action, target, and a
   before/after diff.
3. Publish to Redis channel ``effective-config-invalidate`` so other
   ECS tasks evict their effective-config cache; the admin's own
   process gets a direct in-line eviction in the same call.

The handlers stay thin — anything reusable (diff packing, audit
shape, invalidation broadcast) lives in this module's helpers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.models.command import Command as CommandModel
from lite_horse.models.cron_job import CronJob as CronJobModel
from lite_horse.models.instruction import Instruction as InstructionModel
from lite_horse.models.mcp_server import McpServer as McpServerModel
from lite_horse.models.skill import Skill as SkillModel
from lite_horse.models.usage_event import UsageEvent
from lite_horse.models.user import User
from lite_horse.repositories import (
    AuditRepo,
    CommandRepo,
    CronRepo,
    InstructionRepo,
    McpRepo,
    SkillRepo,
)
from lite_horse.storage.kms import Kms
from lite_horse.storage.redis_client import Redis
from lite_horse.web.context import RequestContext
from lite_horse.web.deps import (
    get_db_session,
    get_kms,
    get_redis,
    require_admin,
)
from lite_horse.web.effective_invalidate import publish_invalidation
from lite_horse.web.errors import ErrorKind, http_error
from lite_horse.web.schemas import (
    AdminCommandCreateIn,
    AdminCommandOut,
    AdminCommandUpdateIn,
    AdminCronJobCreateIn,
    AdminCronJobOut,
    AdminCronJobUpdateIn,
    AdminInstructionCreateIn,
    AdminInstructionOut,
    AdminInstructionUpdateIn,
    AdminMcpServerCreateIn,
    AdminMcpServerOut,
    AdminMcpServerUpdateIn,
    AdminSkillCreateIn,
    AdminSkillOut,
    AdminSkillUpdateIn,
    AdminUsageOut,
    AdminUserOut,
    AuditLogOut,
    McpHealthOut,
    McpHealthRow,
    RollbackIn,
    UsageRow,
    VersionView,
)

router = APIRouter(prefix="/v1/admin", tags=["admin"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
AdminCtx = Annotated[RequestContext, Depends(require_admin)]
RedisDep = Annotated[Redis | None, Depends(get_redis)]
KmsDep = Annotated[Kms, Depends(get_kms)]


# ---------- helpers ----------


async def _audit(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    action: str,
    target: dict[str, Any],
    diff: dict[str, Any] | None = None,
) -> None:
    """Append an audit row for the current admin action."""
    await AuditRepo(session).log(
        actor_id=ctx.user_id,
        actor_role=ctx.role,
        action=action,
        target=target,
        diff=diff,
        request_id=ctx.request_id if _is_uuid(ctx.request_id) else None,
    )


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError):
        return False
    return True


def _diff(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    return {"before": before, "after": after}


# ---------- skills ----------


def _skill_to_admin_out(s: SkillModel) -> AdminSkillOut:
    return AdminSkillOut(
        slug=s.slug,
        version=s.version,
        is_current=s.is_current,
        mandatory=s.mandatory,
        enabled_default=s.enabled_default,
        frontmatter=dict(s.frontmatter),
        body=s.body,
        created_at=s.created_at,
    )


def _skill_snapshot(s: SkillModel) -> dict[str, Any]:
    return {
        "version": s.version,
        "mandatory": s.mandatory,
        "enabled_default": s.enabled_default,
        "frontmatter": dict(s.frontmatter),
        "body": s.body,
    }


@router.get("/skills", response_model=list[AdminSkillOut])
async def list_admin_skills(
    session: DbSession, _: AdminCtx
) -> list[AdminSkillOut]:
    rows = await SkillRepo(session).list_official()
    return [_skill_to_admin_out(r) for r in rows]


@router.post(
    "/skills",
    response_model=AdminSkillOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_skill(
    body: AdminSkillCreateIn,
    session: DbSession,
    ctx: AdminCtx,
    redis: RedisDep,
) -> AdminSkillOut:
    try:
        row = await SkillRepo(session).create_official(
            slug=body.slug,
            frontmatter=body.frontmatter,
            body=body.body,
            mandatory=body.mandatory,
            enabled_default=body.enabled_default,
            created_by=UUID(ctx.user_id),
        )
    except IntegrityError as exc:
        raise http_error(
            ErrorKind.CONFLICT, f"official skill {body.slug!r} already exists"
        ) from exc
    await _audit(
        session,
        ctx,
        action="skill.create",
        target={"entity": "skill", "slug": body.slug},
        diff=_diff(None, _skill_snapshot(row)),
    )
    await publish_invalidation(redis)
    return _skill_to_admin_out(row)


@router.get("/skills/{slug}", response_model=AdminSkillOut)
async def get_admin_skill(
    slug: str, session: DbSession, _: AdminCtx
) -> AdminSkillOut:
    row = await SkillRepo(session).get_official(slug)
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"official skill {slug!r} not found")
    return _skill_to_admin_out(row)


@router.put("/skills/{slug}", response_model=AdminSkillOut)
async def update_admin_skill(
    slug: str,
    body: AdminSkillUpdateIn,
    session: DbSession,
    ctx: AdminCtx,
    redis: RedisDep,
) -> AdminSkillOut:
    repo = SkillRepo(session)
    before = await repo.get_official(slug)
    if before is None:
        raise http_error(ErrorKind.NOT_FOUND, f"official skill {slug!r} not found")
    before_snap = _skill_snapshot(before)
    row = await repo.update_official(
        slug,
        frontmatter=body.frontmatter,
        body=body.body,
        mandatory=body.mandatory,
        enabled_default=body.enabled_default,
        created_by=UUID(ctx.user_id),
    )
    assert row is not None
    await _audit(
        session,
        ctx,
        action="skill.update",
        target={"entity": "skill", "slug": slug},
        diff=_diff(before_snap, _skill_snapshot(row)),
    )
    await publish_invalidation(redis)
    return _skill_to_admin_out(row)


@router.delete("/skills/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin_skill(
    slug: str, session: DbSession, ctx: AdminCtx, redis: RedisDep
) -> None:
    repo = SkillRepo(session)
    before = await repo.get_official(slug)
    if before is None or not await repo.delete_official(slug):
        raise http_error(ErrorKind.NOT_FOUND, f"official skill {slug!r} not found")
    await _audit(
        session,
        ctx,
        action="skill.delete",
        target={"entity": "skill", "slug": slug},
        diff=_diff(_skill_snapshot(before), None),
    )
    await publish_invalidation(redis)


# ---------- instructions ----------


def _instruction_to_admin_out(i: InstructionModel) -> AdminInstructionOut:
    return AdminInstructionOut(
        slug=i.slug,
        version=i.version,
        is_current=i.is_current,
        mandatory=i.mandatory,
        priority=i.priority,
        body=i.body,
        created_at=i.created_at,
    )


def _instruction_snapshot(i: InstructionModel) -> dict[str, Any]:
    return {
        "version": i.version,
        "mandatory": i.mandatory,
        "priority": i.priority,
        "body": i.body,
    }


@router.get("/instructions", response_model=list[AdminInstructionOut])
async def list_admin_instructions(
    session: DbSession, _: AdminCtx
) -> list[AdminInstructionOut]:
    rows = await InstructionRepo(session).list_official()
    return [_instruction_to_admin_out(r) for r in rows]


@router.post(
    "/instructions",
    response_model=AdminInstructionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_instruction(
    body: AdminInstructionCreateIn,
    session: DbSession,
    ctx: AdminCtx,
    redis: RedisDep,
) -> AdminInstructionOut:
    try:
        row = await InstructionRepo(session).create_official(
            slug=body.slug,
            body=body.body,
            priority=body.priority,
            mandatory=body.mandatory,
            created_by=UUID(ctx.user_id),
        )
    except IntegrityError as exc:
        raise http_error(
            ErrorKind.CONFLICT,
            f"official instruction {body.slug!r} already exists",
        ) from exc
    await _audit(
        session,
        ctx,
        action="instruction.create",
        target={"entity": "instruction", "slug": body.slug},
        diff=_diff(None, _instruction_snapshot(row)),
    )
    await publish_invalidation(redis)
    return _instruction_to_admin_out(row)


@router.get("/instructions/{slug}", response_model=AdminInstructionOut)
async def get_admin_instruction(
    slug: str, session: DbSession, _: AdminCtx
) -> AdminInstructionOut:
    row = await InstructionRepo(session).get_official(slug)
    if row is None:
        raise http_error(
            ErrorKind.NOT_FOUND, f"official instruction {slug!r} not found"
        )
    return _instruction_to_admin_out(row)


@router.put("/instructions/{slug}", response_model=AdminInstructionOut)
async def update_admin_instruction(
    slug: str,
    body: AdminInstructionUpdateIn,
    session: DbSession,
    ctx: AdminCtx,
    redis: RedisDep,
) -> AdminInstructionOut:
    repo = InstructionRepo(session)
    before = await repo.get_official(slug)
    if before is None:
        raise http_error(
            ErrorKind.NOT_FOUND, f"official instruction {slug!r} not found"
        )
    before_snap = _instruction_snapshot(before)
    row = await repo.update_official(
        slug,
        body=body.body,
        priority=body.priority,
        mandatory=body.mandatory,
        created_by=UUID(ctx.user_id),
    )
    assert row is not None
    await _audit(
        session,
        ctx,
        action="instruction.update",
        target={"entity": "instruction", "slug": slug},
        diff=_diff(before_snap, _instruction_snapshot(row)),
    )
    await publish_invalidation(redis)
    return _instruction_to_admin_out(row)


@router.delete(
    "/instructions/{slug}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_admin_instruction(
    slug: str, session: DbSession, ctx: AdminCtx, redis: RedisDep
) -> None:
    repo = InstructionRepo(session)
    before = await repo.get_official(slug)
    if before is None or not await repo.delete_official(slug):
        raise http_error(
            ErrorKind.NOT_FOUND, f"official instruction {slug!r} not found"
        )
    await _audit(
        session,
        ctx,
        action="instruction.delete",
        target={"entity": "instruction", "slug": slug},
        diff=_diff(_instruction_snapshot(before), None),
    )
    await publish_invalidation(redis)


# ---------- commands ----------


def _command_to_admin_out(c: CommandModel) -> AdminCommandOut:
    return AdminCommandOut(
        slug=c.slug,
        version=c.version,
        is_current=c.is_current,
        mandatory=c.mandatory,
        description=c.description,
        prompt_tpl=c.prompt_tpl,
        arg_schema=dict(c.arg_schema) if c.arg_schema is not None else None,
        bind_skills=list(c.bind_skills) if c.bind_skills else None,
        created_at=c.created_at,
    )


def _command_snapshot(c: CommandModel) -> dict[str, Any]:
    return {
        "version": c.version,
        "mandatory": c.mandatory,
        "description": c.description,
        "prompt_tpl": c.prompt_tpl,
        "arg_schema": dict(c.arg_schema) if c.arg_schema else None,
        "bind_skills": list(c.bind_skills) if c.bind_skills else None,
    }


@router.get("/commands", response_model=list[AdminCommandOut])
async def list_admin_commands(
    session: DbSession, _: AdminCtx
) -> list[AdminCommandOut]:
    rows = await CommandRepo(session).list_official()
    return [_command_to_admin_out(r) for r in rows]


@router.post(
    "/commands",
    response_model=AdminCommandOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_command(
    body: AdminCommandCreateIn,
    session: DbSession,
    ctx: AdminCtx,
    redis: RedisDep,
) -> AdminCommandOut:
    try:
        row = await CommandRepo(session).create_official(
            slug=body.slug,
            prompt_tpl=body.prompt_tpl,
            description=body.description,
            arg_schema=body.arg_schema,
            bind_skills=body.bind_skills,
            mandatory=body.mandatory,
            created_by=UUID(ctx.user_id),
        )
    except IntegrityError as exc:
        raise http_error(
            ErrorKind.CONFLICT, f"official command {body.slug!r} already exists"
        ) from exc
    await _audit(
        session,
        ctx,
        action="command.create",
        target={"entity": "command", "slug": body.slug},
        diff=_diff(None, _command_snapshot(row)),
    )
    await publish_invalidation(redis)
    return _command_to_admin_out(row)


@router.get("/commands/{slug}", response_model=AdminCommandOut)
async def get_admin_command(
    slug: str, session: DbSession, _: AdminCtx
) -> AdminCommandOut:
    row = await CommandRepo(session).get_official(slug)
    if row is None:
        raise http_error(
            ErrorKind.NOT_FOUND, f"official command {slug!r} not found"
        )
    return _command_to_admin_out(row)


@router.put("/commands/{slug}", response_model=AdminCommandOut)
async def update_admin_command(
    slug: str,
    body: AdminCommandUpdateIn,
    session: DbSession,
    ctx: AdminCtx,
    redis: RedisDep,
) -> AdminCommandOut:
    repo = CommandRepo(session)
    before = await repo.get_official(slug)
    if before is None:
        raise http_error(
            ErrorKind.NOT_FOUND, f"official command {slug!r} not found"
        )
    before_snap = _command_snapshot(before)
    row = await repo.update_official(
        slug,
        prompt_tpl=body.prompt_tpl,
        description=body.description,
        arg_schema=body.arg_schema,
        bind_skills=body.bind_skills,
        mandatory=body.mandatory,
        created_by=UUID(ctx.user_id),
    )
    assert row is not None
    await _audit(
        session,
        ctx,
        action="command.update",
        target={"entity": "command", "slug": slug},
        diff=_diff(before_snap, _command_snapshot(row)),
    )
    await publish_invalidation(redis)
    return _command_to_admin_out(row)


@router.delete(
    "/commands/{slug}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_admin_command(
    slug: str, session: DbSession, ctx: AdminCtx, redis: RedisDep
) -> None:
    repo = CommandRepo(session)
    before = await repo.get_official(slug)
    if before is None or not await repo.delete_official(slug):
        raise http_error(
            ErrorKind.NOT_FOUND, f"official command {slug!r} not found"
        )
    await _audit(
        session,
        ctx,
        action="command.delete",
        target={"entity": "command", "slug": slug},
        diff=_diff(_command_snapshot(before), None),
    )
    await publish_invalidation(redis)


# ---------- mcp servers ----------


def _mcp_to_admin_out(m: McpServerModel) -> AdminMcpServerOut:
    return AdminMcpServerOut(
        slug=m.slug,
        version=m.version,
        is_current=m.is_current,
        url=m.url,
        auth_header=m.auth_header,
        has_auth_value=m.auth_value_ct is not None,
        cache_tools_list=m.cache_tools_list,
        enabled=m.enabled,
        mandatory=m.mandatory,
    )


def _mcp_snapshot(m: McpServerModel) -> dict[str, Any]:
    return {
        "version": m.version,
        "mandatory": m.mandatory,
        "url": m.url,
        "auth_header": m.auth_header,
        "has_auth_value": m.auth_value_ct is not None,
        "cache_tools_list": m.cache_tools_list,
        "enabled": m.enabled,
    }


@router.get("/mcp-servers", response_model=list[AdminMcpServerOut])
async def list_admin_mcp(
    session: DbSession, _: AdminCtx
) -> list[AdminMcpServerOut]:
    rows = await McpRepo(session).list_official()
    return [_mcp_to_admin_out(r) for r in rows]


@router.post(
    "/mcp-servers",
    response_model=AdminMcpServerOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_mcp(
    body: AdminMcpServerCreateIn,
    session: DbSession,
    ctx: AdminCtx,
    kms: KmsDep,
    redis: RedisDep,
) -> AdminMcpServerOut:
    try:
        row = await McpRepo(session).create_official(
            slug=body.slug,
            url=body.url,
            kms=kms,
            auth_header=body.auth_header,
            auth_value=body.auth_value,
            cache_tools_list=body.cache_tools_list,
            enabled=body.enabled,
            mandatory=body.mandatory,
        )
    except IntegrityError as exc:
        raise http_error(
            ErrorKind.CONFLICT, f"official mcp {body.slug!r} already exists"
        ) from exc
    await _audit(
        session,
        ctx,
        action="mcp_server.create",
        target={"entity": "mcp_server", "slug": body.slug},
        diff=_diff(None, _mcp_snapshot(row)),
    )
    await publish_invalidation(redis)
    return _mcp_to_admin_out(row)


@router.get("/mcp-servers/{slug}", response_model=AdminMcpServerOut)
async def get_admin_mcp(
    slug: str, session: DbSession, _: AdminCtx
) -> AdminMcpServerOut:
    row = await McpRepo(session).get_official(slug)
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"official mcp {slug!r} not found")
    return _mcp_to_admin_out(row)


@router.put("/mcp-servers/{slug}", response_model=AdminMcpServerOut)
async def update_admin_mcp(
    slug: str,
    body: AdminMcpServerUpdateIn,
    session: DbSession,
    ctx: AdminCtx,
    kms: KmsDep,
    redis: RedisDep,
) -> AdminMcpServerOut:
    repo = McpRepo(session)
    before = await repo.get_official(slug)
    if before is None:
        raise http_error(ErrorKind.NOT_FOUND, f"official mcp {slug!r} not found")
    before_snap = _mcp_snapshot(before)
    row = await repo.update_official(
        slug,
        kms=kms,
        url=body.url,
        auth_header=body.auth_header,
        auth_value=body.auth_value,
        clear_auth_value=body.clear_auth_value,
        cache_tools_list=body.cache_tools_list,
        enabled=body.enabled,
        mandatory=body.mandatory,
    )
    assert row is not None
    await _audit(
        session,
        ctx,
        action="mcp_server.update",
        target={"entity": "mcp_server", "slug": slug},
        diff=_diff(before_snap, _mcp_snapshot(row)),
    )
    await publish_invalidation(redis)
    return _mcp_to_admin_out(row)


@router.delete(
    "/mcp-servers/{slug}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_admin_mcp(
    slug: str, session: DbSession, ctx: AdminCtx, redis: RedisDep
) -> None:
    repo = McpRepo(session)
    before = await repo.get_official(slug)
    if before is None or not await repo.delete_official(slug):
        raise http_error(ErrorKind.NOT_FOUND, f"official mcp {slug!r} not found")
    await _audit(
        session,
        ctx,
        action="mcp_server.delete",
        target={"entity": "mcp_server", "slug": slug},
        diff=_diff(_mcp_snapshot(before), None),
    )
    await publish_invalidation(redis)


# ---------- cron jobs ----------


def _cron_to_admin_out(c: CronJobModel) -> AdminCronJobOut:
    return AdminCronJobOut(
        slug=c.slug,
        cron_expr=c.cron_expr,
        prompt=c.prompt,
        webhook_url=c.webhook_url,
        enabled=c.enabled,
        mandatory=c.mandatory,
    )


def _cron_snapshot(c: CronJobModel) -> dict[str, Any]:
    return {
        "cron_expr": c.cron_expr,
        "prompt": c.prompt,
        "webhook_url": c.webhook_url,
        "enabled": c.enabled,
        "mandatory": c.mandatory,
    }


@router.get("/cron-jobs", response_model=list[AdminCronJobOut])
async def list_admin_cron(
    session: DbSession, _: AdminCtx
) -> list[AdminCronJobOut]:
    rows = await CronRepo(session).list_official()
    return [_cron_to_admin_out(r) for r in rows]


@router.post(
    "/cron-jobs",
    response_model=AdminCronJobOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_cron(
    body: AdminCronJobCreateIn,
    session: DbSession,
    ctx: AdminCtx,
    redis: RedisDep,
) -> AdminCronJobOut:
    try:
        row = await CronRepo(session).create_official(
            slug=body.slug,
            cron_expr=body.cron_expr,
            prompt=body.prompt,
            webhook_url=body.webhook_url,
            enabled=body.enabled,
            mandatory=body.mandatory,
        )
    except IntegrityError as exc:
        raise http_error(
            ErrorKind.CONFLICT, f"official cron {body.slug!r} already exists"
        ) from exc
    await _audit(
        session,
        ctx,
        action="cron_job.create",
        target={"entity": "cron_job", "slug": body.slug},
        diff=_diff(None, _cron_snapshot(row)),
    )
    await publish_invalidation(redis)
    return _cron_to_admin_out(row)


@router.get("/cron-jobs/{slug}", response_model=AdminCronJobOut)
async def get_admin_cron(
    slug: str, session: DbSession, _: AdminCtx
) -> AdminCronJobOut:
    row = await CronRepo(session).get_official(slug)
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"official cron {slug!r} not found")
    return _cron_to_admin_out(row)


@router.put("/cron-jobs/{slug}", response_model=AdminCronJobOut)
async def update_admin_cron(
    slug: str,
    body: AdminCronJobUpdateIn,
    session: DbSession,
    ctx: AdminCtx,
    redis: RedisDep,
) -> AdminCronJobOut:
    repo = CronRepo(session)
    before = await repo.get_official(slug)
    if before is None:
        raise http_error(ErrorKind.NOT_FOUND, f"official cron {slug!r} not found")
    before_snap = _cron_snapshot(before)
    row = await repo.update_official(
        slug,
        cron_expr=body.cron_expr,
        prompt=body.prompt,
        webhook_url=body.webhook_url,
        clear_webhook_url=body.clear_webhook_url,
        enabled=body.enabled,
        mandatory=body.mandatory,
    )
    assert row is not None
    await _audit(
        session,
        ctx,
        action="cron_job.update",
        target={"entity": "cron_job", "slug": slug},
        diff=_diff(before_snap, _cron_snapshot(row)),
    )
    await publish_invalidation(redis)
    return _cron_to_admin_out(row)


@router.delete(
    "/cron-jobs/{slug}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_admin_cron(
    slug: str, session: DbSession, ctx: AdminCtx, redis: RedisDep
) -> None:
    repo = CronRepo(session)
    before = await repo.get_official(slug)
    if before is None or not await repo.delete_official(slug):
        raise http_error(ErrorKind.NOT_FOUND, f"official cron {slug!r} not found")
    await _audit(
        session,
        ctx,
        action="cron_job.delete",
        target={"entity": "cron_job", "slug": slug},
        diff=_diff(_cron_snapshot(before), None),
    )
    await publish_invalidation(redis)


# ---------- versions + rollback ----------


_VERSIONED_REPOS: dict[str, type] = {
    "skills": SkillRepo,
    "instructions": InstructionRepo,
    "commands": CommandRepo,
    "mcp-servers": McpRepo,
}


def _versioned_repo(entity: str, session: AsyncSession) -> Any:
    repo_cls = _VERSIONED_REPOS.get(entity)
    if repo_cls is None:
        raise http_error(
            ErrorKind.NOT_FOUND, f"{entity!r} does not support versioning"
        )
    return repo_cls(session)


@router.get(
    "/{entity}/{slug}/versions", response_model=list[VersionView]
)
async def list_versions(
    entity: str, slug: str, session: DbSession, _: AdminCtx
) -> list[VersionView]:
    repo = _versioned_repo(entity, session)
    rows = await repo.list_versions_official(slug)
    if not rows:
        raise http_error(ErrorKind.NOT_FOUND, f"{entity}/{slug} has no versions")
    return [
        VersionView(
            version=r.version,
            is_current=r.is_current,
            mandatory=r.mandatory,
            created_at=getattr(r, "created_at", None),
        )
        for r in rows
    ]


@router.post("/{entity}/{slug}:rollback")
async def rollback_official(
    entity: str,
    slug: str,
    body: RollbackIn,
    session: DbSession,
    ctx: AdminCtx,
    redis: RedisDep,
) -> dict[str, Any]:
    repo = _versioned_repo(entity, session)
    target = await repo.rollback_official(slug, body.version)
    if target is None:
        raise http_error(
            ErrorKind.NOT_FOUND,
            f"{entity}/{slug} has no version {body.version}",
        )
    await _audit(
        session,
        ctx,
        action=f"{entity[:-1] if entity.endswith('s') else entity}.rollback",
        target={"entity": entity, "slug": slug},
        diff={"rolled_back_to_version": body.version},
    )
    await publish_invalidation(redis)
    return {"slug": slug, "version": target.version, "is_current": target.is_current}


# ---------- users ----------


@router.get("/users", response_model=list[AdminUserOut])
async def list_admin_users(
    session: DbSession, _: AdminCtx
) -> list[AdminUserOut]:
    stmt = select(User).order_by(User.created_at.desc()).limit(500)
    rows = list((await session.execute(stmt)).scalars().all())
    return [
        AdminUserOut(
            id=str(u.id),
            external_id=u.external_id,
            role=u.role,
            created_at=u.created_at,
        )
        for u in rows
    ]


@router.get("/users/{user_id}/usage", response_model=AdminUsageOut)
async def get_user_usage(
    user_id: str, session: DbSession, _: AdminCtx
) -> AdminUsageOut:
    try:
        uid = UUID(user_id)
    except ValueError as exc:
        raise http_error(ErrorKind.NOT_FOUND, "invalid user_id") from exc
    day = func.date(UsageEvent.ts).label("day")
    stmt = (
        select(
            day,
            UsageEvent.model,
            func.coalesce(func.sum(UsageEvent.input_tokens), 0).label("in_tok"),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0).label("out_tok"),
            func.coalesce(func.sum(UsageEvent.cost_usd_micro), 0).label("cost"),
        )
        .where(UsageEvent.user_id == uid)
        .group_by(day, UsageEvent.model)
        .order_by(day.desc(), UsageEvent.model)
    )
    rows = (await session.execute(stmt)).all()
    return AdminUsageOut(
        user_id=user_id,
        rows=[
            UsageRow(
                day=str(r.day),
                model=str(r.model),
                input_tokens=int(r.in_tok),
                output_tokens=int(r.out_tok),
                cost_usd_micro=int(r.cost),
            )
            for r in rows
        ],
    )


# ---------- audit log ----------


@router.get("/audit-log", response_model=list[AuditLogOut])
async def list_audit_log(
    session: DbSession,
    _: AdminCtx,
    actor_id: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[AuditLogOut]:
    rows = await AuditRepo(session).list(
        actor_id=actor_id, action=action, since=since, limit=limit
    )
    return [
        AuditLogOut(
            id=r.id,
            ts=r.ts,
            actor_id=str(r.actor_id),
            actor_role=r.actor_role,
            action=r.action,
            target=dict(r.target),
            diff=dict(r.diff) if r.diff else None,
            request_id=str(r.request_id) if r.request_id else None,
        )
        for r in rows
    ]


# ---------- mcp health ----------


@router.get("/health/mcp", response_model=McpHealthOut)
async def mcp_health(session: DbSession, _: AdminCtx) -> McpHealthOut:
    """Best-effort HEAD probe across every official MCP server.

    A 5 s timeout per server keeps the route bounded under failure; the
    full handshake-level probe lands in a future phase.
    """
    rows = await McpRepo(session).list_official()
    out: list[McpHealthRow] = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for r in rows:
            ok = False
            detail: str | None = None
            try:
                resp = await client.head(r.url)
                ok = resp.status_code < 500
                detail = f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                detail = str(exc)
            out.append(McpHealthRow(slug=r.slug, url=r.url, ok=ok, detail=detail))
    return McpHealthOut(rows=out)
