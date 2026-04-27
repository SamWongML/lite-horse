"""Pydantic request/response models for ``/v1/users/me/*``.

Every entity has:

* ``<X>Out`` — what the route returns. Strips internal columns
  (``user_id``, ``id``, ``created_by``) and never leaks ciphertext.
* ``<X>CreateIn`` / ``<X>UpdateIn`` — request bodies. ``UpdateIn``
  fields are all optional so ``PUT`` semantics are partial-update
  (the underlying repo's ``update_user`` does the same).

The route handlers are responsible for converting between ORM rows and
these shapes; the repos stay pure SQLAlchemy.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------- memory / user-doc ----------


class DocumentOut(BaseModel):
    content: str


class DocumentIn(BaseModel):
    content: str


# ---------- settings ----------


class SettingsOut(BaseModel):
    default_model: str | None
    permission_mode: Literal["auto", "ask", "ro"]


class SettingsIn(BaseModel):
    default_model: str | None = None
    clear_default_model: bool = False
    permission_mode: Literal["auto", "ask", "ro"] | None = None


# ---------- skills ----------


class SkillOut(BaseModel):
    slug: str
    version: int
    enabled_default: bool
    frontmatter: dict[str, Any]
    body: str


class SkillCreateIn(BaseModel):
    slug: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str
    enabled_default: bool = True


class SkillUpdateIn(BaseModel):
    frontmatter: dict[str, Any] | None = None
    body: str | None = None
    enabled_default: bool | None = None


# ---------- instructions ----------


class InstructionOut(BaseModel):
    slug: str
    version: int
    priority: int
    body: str


class InstructionCreateIn(BaseModel):
    slug: str
    body: str
    priority: int = 100


class InstructionUpdateIn(BaseModel):
    body: str | None = None
    priority: int | None = None


# ---------- commands ----------


class CommandOut(BaseModel):
    slug: str
    version: int
    description: str | None
    prompt_tpl: str
    arg_schema: dict[str, Any] | None
    bind_skills: list[str] | None


class CommandCreateIn(BaseModel):
    slug: str
    prompt_tpl: str
    description: str | None = None
    arg_schema: dict[str, Any] | None = None
    bind_skills: list[str] | None = None


class CommandUpdateIn(BaseModel):
    prompt_tpl: str | None = None
    description: str | None = None
    arg_schema: dict[str, Any] | None = None
    bind_skills: list[str] | None = None


# ---------- mcp servers ----------


class McpServerOut(BaseModel):
    """Public MCP server view — never leaks the plaintext or ciphertext auth."""

    slug: str
    url: str
    auth_header: str | None
    has_auth_value: bool
    cache_tools_list: bool
    enabled: bool
    last_probe_at: datetime | None
    last_probe_ok: bool | None


class McpServerCreateIn(BaseModel):
    slug: str
    url: str
    auth_header: str | None = None
    auth_value: str | None = None
    cache_tools_list: bool = True
    enabled: bool = True


class McpServerUpdateIn(BaseModel):
    url: str | None = None
    auth_header: str | None = None
    auth_value: str | None = None
    clear_auth_value: bool = False
    cache_tools_list: bool | None = None
    enabled: bool | None = None


class McpProbeResult(BaseModel):
    ok: bool
    when: datetime
    detail: str | None = None


# ---------- cron jobs ----------


class CronJobOut(BaseModel):
    slug: str
    cron_expr: str
    prompt: str
    webhook_url: str | None
    enabled: bool
    last_fired_at: datetime | None


class CronJobCreateIn(BaseModel):
    slug: str
    cron_expr: str
    prompt: str
    webhook_url: str | None = None
    enabled: bool = True


class CronJobUpdateIn(BaseModel):
    cron_expr: str | None = None
    prompt: str | None = None
    webhook_url: str | None = None
    clear_webhook_url: bool = False
    enabled: bool | None = None


# ---------- opt-outs ----------


class OptOutIn(BaseModel):
    entity: Literal["skill", "instruction", "command", "mcp_server", "cron_job"]
    slug: str


class OptOutOut(BaseModel):
    entity: str
    slug: str


# ---------- effective config ----------


class ResolvedSkillView(BaseModel):
    slug: str
    scope: Literal["bundled", "official", "user"]
    description: str
    enabled_default: bool
    mandatory: bool
    frontmatter: dict[str, Any]


class ResolvedInstructionView(BaseModel):
    slug: str
    scope: Literal["bundled", "official", "user"]
    body: str
    priority: int
    mandatory: bool


class ResolvedCommandView(BaseModel):
    slug: str
    scope: Literal["bundled", "official", "user"]
    description: str | None
    prompt_tpl: str
    bind_skills: list[str]


class ResolvedMcpServerView(BaseModel):
    """MCP server in the resolver output — sanitised for the client.

    Plaintext auth values never reach this layer, and we strip the
    ciphertext too — the only metadata that leaves the server is
    ``has_auth_value`` so a UI can render an "auth set" indicator.
    """

    slug: str
    scope: Literal["bundled", "official", "user"]
    url: str
    auth_header: str | None
    has_auth_value: bool
    cache_tools_list: bool
    enabled: bool
    mandatory: bool


class EffectiveConfigView(BaseModel):
    skills: list[ResolvedSkillView]
    instructions: list[ResolvedInstructionView]
    commands: list[ResolvedCommandView]
    mcp_servers: list[ResolvedMcpServerView]
    etag: str
