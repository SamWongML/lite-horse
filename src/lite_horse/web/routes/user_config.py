"""``/v1/users/me/*`` — per-user CRUD over the layered config.

One router per the locked HTTP surface (see plan §"Locked HTTP surface").
Every route is JWT-gated via :func:`get_db_session` (which depends on
:func:`get_request_context`) so the ``app.user_id`` GUC is set for the
duration of the transaction and RLS enforces tenant isolation.

The MCP endpoints accept the plaintext ``auth_value`` on write, route it
through KMS (with ``EncryptionContext={"user_id": ...}``), and store
ciphertext only. Read paths NEVER surface either the plaintext or the
ciphertext — the public response shape exposes ``has_auth_value: bool``
so the client can render an "auth set" indicator.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.models.command import Command as CommandModel
from lite_horse.models.cron_job import CronJob as CronJobModel
from lite_horse.models.instruction import Instruction as InstructionModel
from lite_horse.models.mcp_server import McpServer as McpServerModel
from lite_horse.models.skill import Skill as SkillModel
from lite_horse.repositories import (
    CommandRepo,
    CronRepo,
    InstructionRepo,
    McpRepo,
    MemoryFull,
    MemoryRepo,
    OptOutRepo,
    SkillRepo,
    UnsafeMemoryContent,
    UserSettingsRepo,
)
from lite_horse.repositories.opt_out_repo import VALID_ENTITIES
from lite_horse.storage.kms import Kms
from lite_horse.storage.redis_client import Redis
from lite_horse.web.context import RequestContext
from lite_horse.web.deps import (
    get_db_session,
    get_kms,
    get_redis,
    get_request_context,
)
from lite_horse.web.effective_cache import get_or_compute_effective_config
from lite_horse.web.errors import ErrorKind, http_error
from lite_horse.web.schemas import (
    CommandCreateIn,
    CommandOut,
    CommandUpdateIn,
    CronJobCreateIn,
    CronJobOut,
    CronJobUpdateIn,
    DocumentIn,
    DocumentOut,
    EffectiveConfigView,
    InstructionCreateIn,
    InstructionOut,
    InstructionUpdateIn,
    McpProbeResult,
    McpServerCreateIn,
    McpServerOut,
    McpServerUpdateIn,
    OptOutIn,
    OptOutOut,
    ResolvedCommandView,
    ResolvedInstructionView,
    ResolvedMcpServerView,
    ResolvedSkillView,
    SettingsIn,
    SettingsOut,
    SkillCreateIn,
    SkillOut,
    SkillUpdateIn,
)

router = APIRouter(prefix="/v1/users/me", tags=["user-config"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
Ctx = Annotated[RequestContext, Depends(get_request_context)]
RedisDep = Annotated[Redis | None, Depends(get_redis)]
KmsDep = Annotated[Kms, Depends(get_kms)]


# ---------- memory + user-doc ----------


@router.get("/memory", response_model=DocumentOut)
async def get_memory(session: DbSession) -> DocumentOut:
    content = await MemoryRepo(session).get("memory.md")
    return DocumentOut(content=content)


@router.put("/memory", response_model=DocumentOut)
async def put_memory(body: DocumentIn, session: DbSession) -> DocumentOut:
    try:
        await MemoryRepo(session).put("memory.md", body.content)
    except MemoryFull as exc:
        raise http_error(ErrorKind.CONFLICT, str(exc)) from exc
    except UnsafeMemoryContent as exc:
        raise http_error(ErrorKind.CONFLICT, str(exc)) from exc
    return DocumentOut(content=body.content)


@router.get("/user-doc", response_model=DocumentOut)
async def get_user_doc(session: DbSession) -> DocumentOut:
    content = await MemoryRepo(session).get("user.md")
    return DocumentOut(content=content)


@router.put("/user-doc", response_model=DocumentOut)
async def put_user_doc(body: DocumentIn, session: DbSession) -> DocumentOut:
    try:
        await MemoryRepo(session).put("user.md", body.content)
    except MemoryFull as exc:
        raise http_error(ErrorKind.CONFLICT, str(exc)) from exc
    except UnsafeMemoryContent as exc:
        raise http_error(ErrorKind.CONFLICT, str(exc)) from exc
    return DocumentOut(content=body.content)


# ---------- settings ----------


@router.get("/settings", response_model=SettingsOut)
async def get_settings_route(session: DbSession) -> SettingsOut:
    settings = await UserSettingsRepo(session).get()
    return SettingsOut(
        default_model=settings.default_model,
        permission_mode=settings.permission_mode,  # type: ignore[arg-type]
    )


@router.put("/settings", response_model=SettingsOut)
async def put_settings(body: SettingsIn, session: DbSession) -> SettingsOut:
    try:
        settings = await UserSettingsRepo(session).update(
            default_model=body.default_model,
            clear_default_model=body.clear_default_model,
            permission_mode=body.permission_mode,
        )
    except ValueError as exc:
        raise http_error(ErrorKind.CONFLICT, str(exc)) from exc
    return SettingsOut(
        default_model=settings.default_model,
        permission_mode=settings.permission_mode,  # type: ignore[arg-type]
    )


# ---------- skills ----------


def _skill_to_out(s: SkillModel) -> SkillOut:
    return SkillOut(
        slug=s.slug,
        version=s.version,
        enabled_default=s.enabled_default,
        frontmatter=dict(s.frontmatter),
        body=s.body,
    )


@router.get("/skills", response_model=list[SkillOut])
async def list_skills(session: DbSession) -> list[SkillOut]:
    rows = await SkillRepo(session).list_user()
    return [_skill_to_out(r) for r in rows]


@router.post(
    "/skills",
    response_model=SkillOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_skill(body: SkillCreateIn, session: DbSession) -> SkillOut:
    try:
        row = await SkillRepo(session).create_user(
            slug=body.slug,
            frontmatter=body.frontmatter,
            body=body.body,
            enabled_default=body.enabled_default,
        )
    except IntegrityError as exc:
        raise http_error(
            ErrorKind.CONFLICT, f"skill {body.slug!r} already exists"
        ) from exc
    return _skill_to_out(row)


@router.get("/skills/{slug}", response_model=SkillOut)
async def get_skill(slug: str, session: DbSession) -> SkillOut:
    row = await SkillRepo(session).get_user(slug)
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"skill {slug!r} not found")
    return _skill_to_out(row)


@router.put("/skills/{slug}", response_model=SkillOut)
async def update_skill(
    slug: str, body: SkillUpdateIn, session: DbSession
) -> SkillOut:
    row = await SkillRepo(session).update_user(
        slug,
        frontmatter=body.frontmatter,
        body=body.body,
        enabled_default=body.enabled_default,
    )
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"skill {slug!r} not found")
    return _skill_to_out(row)


@router.delete("/skills/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(slug: str, session: DbSession) -> None:
    if not await SkillRepo(session).delete_user(slug):
        raise http_error(ErrorKind.NOT_FOUND, f"skill {slug!r} not found")


# ---------- instructions ----------


def _instruction_to_out(i: InstructionModel) -> InstructionOut:
    return InstructionOut(
        slug=i.slug, version=i.version, priority=i.priority, body=i.body
    )


@router.get("/instructions", response_model=list[InstructionOut])
async def list_instructions(session: DbSession) -> list[InstructionOut]:
    rows = await InstructionRepo(session).list_user()
    return [_instruction_to_out(r) for r in rows]


@router.post(
    "/instructions",
    response_model=InstructionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_instruction(
    body: InstructionCreateIn, session: DbSession
) -> InstructionOut:
    try:
        row = await InstructionRepo(session).create_user(
            slug=body.slug, body=body.body, priority=body.priority
        )
    except IntegrityError as exc:
        raise http_error(
            ErrorKind.CONFLICT, f"instruction {body.slug!r} already exists"
        ) from exc
    return _instruction_to_out(row)


@router.get("/instructions/{slug}", response_model=InstructionOut)
async def get_instruction(slug: str, session: DbSession) -> InstructionOut:
    row = await InstructionRepo(session).get_user(slug)
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"instruction {slug!r} not found")
    return _instruction_to_out(row)


@router.put("/instructions/{slug}", response_model=InstructionOut)
async def update_instruction(
    slug: str, body: InstructionUpdateIn, session: DbSession
) -> InstructionOut:
    row = await InstructionRepo(session).update_user(
        slug, body=body.body, priority=body.priority
    )
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"instruction {slug!r} not found")
    return _instruction_to_out(row)


@router.delete(
    "/instructions/{slug}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_instruction(slug: str, session: DbSession) -> None:
    if not await InstructionRepo(session).delete_user(slug):
        raise http_error(ErrorKind.NOT_FOUND, f"instruction {slug!r} not found")


# ---------- commands ----------


def _command_to_out(c: CommandModel) -> CommandOut:
    schema: dict[str, Any] | None
    schema = dict(c.arg_schema) if c.arg_schema is not None else None
    return CommandOut(
        slug=c.slug,
        version=c.version,
        description=c.description,
        prompt_tpl=c.prompt_tpl,
        arg_schema=schema,
        bind_skills=list(c.bind_skills) if c.bind_skills else None,
    )


@router.get("/commands", response_model=list[CommandOut])
async def list_commands(session: DbSession) -> list[CommandOut]:
    rows = await CommandRepo(session).list_user()
    return [_command_to_out(r) for r in rows]


@router.post(
    "/commands",
    response_model=CommandOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_command(body: CommandCreateIn, session: DbSession) -> CommandOut:
    try:
        row = await CommandRepo(session).create_user(
            slug=body.slug,
            prompt_tpl=body.prompt_tpl,
            description=body.description,
            arg_schema=body.arg_schema,
            bind_skills=body.bind_skills,
        )
    except IntegrityError as exc:
        raise http_error(
            ErrorKind.CONFLICT, f"command {body.slug!r} already exists"
        ) from exc
    return _command_to_out(row)


@router.get("/commands/{slug}", response_model=CommandOut)
async def get_command(slug: str, session: DbSession) -> CommandOut:
    row = await CommandRepo(session).get_user(slug)
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"command {slug!r} not found")
    return _command_to_out(row)


@router.put("/commands/{slug}", response_model=CommandOut)
async def update_command(
    slug: str, body: CommandUpdateIn, session: DbSession
) -> CommandOut:
    row = await CommandRepo(session).update_user(
        slug,
        prompt_tpl=body.prompt_tpl,
        description=body.description,
        arg_schema=body.arg_schema,
        bind_skills=body.bind_skills,
    )
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"command {slug!r} not found")
    return _command_to_out(row)


@router.delete(
    "/commands/{slug}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_command(slug: str, session: DbSession) -> None:
    if not await CommandRepo(session).delete_user(slug):
        raise http_error(ErrorKind.NOT_FOUND, f"command {slug!r} not found")


# ---------- mcp servers ----------


def _mcp_to_out(m: McpServerModel) -> McpServerOut:
    return McpServerOut(
        slug=m.slug,
        url=m.url,
        auth_header=m.auth_header,
        has_auth_value=m.auth_value_ct is not None,
        cache_tools_list=m.cache_tools_list,
        enabled=m.enabled,
        last_probe_at=m.last_probe_at,
        last_probe_ok=m.last_probe_ok,
    )


@router.get("/mcp-servers", response_model=list[McpServerOut])
async def list_mcp(session: DbSession) -> list[McpServerOut]:
    rows = await McpRepo(session).list_user()
    return [_mcp_to_out(r) for r in rows]


@router.post(
    "/mcp-servers",
    response_model=McpServerOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_mcp(
    body: McpServerCreateIn, session: DbSession, kms: KmsDep
) -> McpServerOut:
    try:
        row = await McpRepo(session).create_user(
            slug=body.slug,
            url=body.url,
            kms=kms,
            auth_header=body.auth_header,
            auth_value=body.auth_value,
            cache_tools_list=body.cache_tools_list,
            enabled=body.enabled,
        )
    except IntegrityError as exc:
        raise http_error(
            ErrorKind.CONFLICT, f"mcp_server {body.slug!r} already exists"
        ) from exc
    return _mcp_to_out(row)


@router.get("/mcp-servers/{slug}", response_model=McpServerOut)
async def get_mcp(slug: str, session: DbSession) -> McpServerOut:
    row = await McpRepo(session).get_user(slug)
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"mcp_server {slug!r} not found")
    return _mcp_to_out(row)


@router.put("/mcp-servers/{slug}", response_model=McpServerOut)
async def update_mcp(
    slug: str,
    body: McpServerUpdateIn,
    session: DbSession,
    kms: KmsDep,
) -> McpServerOut:
    row = await McpRepo(session).update_user(
        slug,
        kms=kms,
        url=body.url,
        auth_header=body.auth_header,
        auth_value=body.auth_value,
        clear_auth_value=body.clear_auth_value,
        cache_tools_list=body.cache_tools_list,
        enabled=body.enabled,
    )
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"mcp_server {slug!r} not found")
    return _mcp_to_out(row)


@router.delete(
    "/mcp-servers/{slug}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_mcp(slug: str, session: DbSession) -> None:
    if not await McpRepo(session).delete_user(slug):
        raise http_error(ErrorKind.NOT_FOUND, f"mcp_server {slug!r} not found")


@router.post(
    "/mcp-servers/{slug}:probe", response_model=McpProbeResult
)
async def probe_mcp(slug: str, session: DbSession) -> McpProbeResult:
    """Best-effort reachability check on the MCP URL.

    Issues an unauthenticated HTTP HEAD with a 5 s timeout. The encrypted
    auth header isn't decrypted here — callers want a quick liveness
    signal, not a full handshake. The full MCP handshake probe lands in
    Phase 34 under ``GET /v1/admin/health/mcp``.
    """
    repo = McpRepo(session)
    row = await repo.get_user(slug)
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"mcp_server {slug!r} not found")
    when = datetime.now(UTC)
    detail: str | None = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.head(row.url)
        ok = resp.status_code < 500
        detail = f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        ok = False
        detail = str(exc)
    await repo.record_probe(slug, ok=ok, when=when)
    return McpProbeResult(ok=ok, when=when, detail=detail)


# ---------- cron jobs ----------


def _cron_to_out(c: CronJobModel) -> CronJobOut:
    return CronJobOut(
        slug=c.slug,
        cron_expr=c.cron_expr,
        prompt=c.prompt,
        webhook_url=c.webhook_url,
        enabled=c.enabled,
        last_fired_at=c.last_fired_at,
    )


@router.get("/cron-jobs", response_model=list[CronJobOut])
async def list_cron(session: DbSession) -> list[CronJobOut]:
    rows = await CronRepo(session).list_user()
    return [_cron_to_out(r) for r in rows]


@router.post(
    "/cron-jobs",
    response_model=CronJobOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_cron(body: CronJobCreateIn, session: DbSession) -> CronJobOut:
    try:
        row = await CronRepo(session).create_user(
            slug=body.slug,
            cron_expr=body.cron_expr,
            prompt=body.prompt,
            webhook_url=body.webhook_url,
            enabled=body.enabled,
        )
    except IntegrityError as exc:
        raise http_error(
            ErrorKind.CONFLICT, f"cron_job {body.slug!r} already exists"
        ) from exc
    return _cron_to_out(row)


@router.get("/cron-jobs/{slug}", response_model=CronJobOut)
async def get_cron(slug: str, session: DbSession) -> CronJobOut:
    row = await CronRepo(session).get_user(slug)
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"cron_job {slug!r} not found")
    return _cron_to_out(row)


@router.put("/cron-jobs/{slug}", response_model=CronJobOut)
async def update_cron(
    slug: str, body: CronJobUpdateIn, session: DbSession
) -> CronJobOut:
    row = await CronRepo(session).update_user(
        slug,
        cron_expr=body.cron_expr,
        prompt=body.prompt,
        webhook_url=body.webhook_url,
        clear_webhook_url=body.clear_webhook_url,
        enabled=body.enabled,
    )
    if row is None:
        raise http_error(ErrorKind.NOT_FOUND, f"cron_job {slug!r} not found")
    return _cron_to_out(row)


@router.delete(
    "/cron-jobs/{slug}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_cron(slug: str, session: DbSession) -> None:
    if not await CronRepo(session).delete_user(slug):
        raise http_error(ErrorKind.NOT_FOUND, f"cron_job {slug!r} not found")


# ---------- opt-outs ----------


@router.get("/opt-outs", response_model=list[OptOutOut])
async def list_opt_outs(session: DbSession) -> list[OptOutOut]:
    rows = await OptOutRepo(session).list()
    return [OptOutOut(entity=e, slug=s) for e, s in rows]


@router.post(
    "/opt-outs",
    response_model=OptOutOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_opt_out(body: OptOutIn, session: DbSession) -> OptOutOut:
    if body.entity not in VALID_ENTITIES:
        raise http_error(
            ErrorKind.CONFLICT, f"invalid opt-out entity: {body.entity!r}"
        )
    await OptOutRepo(session).add(body.entity, body.slug)
    return OptOutOut(entity=body.entity, slug=body.slug)


@router.delete(
    "/opt-outs/{entity}/{slug}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_opt_out(
    entity: str, slug: str, session: DbSession
) -> None:
    if entity not in VALID_ENTITIES:
        raise http_error(
            ErrorKind.NOT_FOUND, f"invalid opt-out entity: {entity!r}"
        )
    if not await OptOutRepo(session).remove(entity, slug):
        raise http_error(
            ErrorKind.NOT_FOUND, f"opt-out for {entity}:{slug!r} not found"
        )


# ---------- effective-config ----------


@router.get("/effective-config", response_model=EffectiveConfigView)
async def get_effective_config(
    session: DbSession,
    ctx: Ctx,
    redis: RedisDep,
) -> EffectiveConfigView:
    eff = await get_or_compute_effective_config(
        session, redis=redis, user_id=ctx.user_id
    )
    return EffectiveConfigView(
        skills=[
            ResolvedSkillView(
                slug=s.slug,
                scope=s.scope,
                description=s.description,
                enabled_default=s.enabled_default,
                mandatory=s.mandatory,
                frontmatter=dict(s.frontmatter),
            )
            for s in eff.skills
        ],
        instructions=[
            ResolvedInstructionView(
                slug=i.slug,
                scope=i.scope,
                body=i.body,
                priority=i.priority,
                mandatory=i.mandatory,
            )
            for i in eff.instructions
        ],
        commands=[
            ResolvedCommandView(
                slug=c.slug,
                scope=c.scope,
                description=c.description,
                prompt_tpl=c.prompt_tpl,
                bind_skills=list(c.bind_skills),
            )
            for c in eff.commands
        ],
        mcp_servers=[
            ResolvedMcpServerView(
                slug=m.slug,
                scope=m.scope,
                url=m.url,
                auth_header=m.auth_header,
                has_auth_value=m.auth_value_ct is not None,
                cache_tools_list=m.cache_tools_list,
                enabled=m.enabled,
                mandatory=m.mandatory,
            )
            for m in eff.mcp_servers
        ],
        etag=eff.etag,
    )
