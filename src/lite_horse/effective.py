"""Resolved-entity dataclasses + EffectiveConfig.

These are the "after the resolver has done its job" shapes. They flatten
the bundled / official / user trichotomy into one list per entity kind so
downstream consumers (agent factory, instructions composer, HTTP route)
can iterate without re-deriving the layered rules.

Lives at package root rather than under ``web/`` so repositories can
import the types without an import cycle (``web/effective_config.py``
imports the repos).
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

Scope = Literal["bundled", "official", "user"]


@dataclass(frozen=True)
class ResolvedSkill:
    slug: str
    scope: Scope
    description: str
    body: str
    frontmatter: dict[str, Any]
    enabled_default: bool = True
    mandatory: bool = False


@dataclass(frozen=True)
class ResolvedInstruction:
    slug: str
    scope: Scope
    body: str
    priority: int = 100
    mandatory: bool = False


@dataclass(frozen=True)
class ResolvedCommand:
    slug: str
    scope: Scope
    prompt_tpl: str
    description: str | None = None
    arg_schema: dict[str, Any] | None = None
    bind_skills: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResolvedMcpServer:
    """MCP server entry as the resolver sees it.

    ``auth_value_ct`` stays opaque ciphertext here; the agent factory
    decrypts it via :class:`lite_horse.storage.kms.Kms` when actually
    constructing the SDK ``MCPServer`` instance. Keeping plaintext out
    of this layer means the HTTP serializer can return ``EffectiveConfig``
    safely without leaking secrets.
    """

    slug: str
    scope: Scope
    url: str
    auth_header: str | None = None
    auth_value_ct: bytes | None = None
    cache_tools_list: bool = True
    enabled: bool = True
    mandatory: bool = False
    user_id: str | None = None  # encryption-context owner; None for official


@dataclass(frozen=True)
class EffectiveConfig:
    """The full resolved config for one user at one moment.

    ``etag`` is a content hash over the lists; cache invalidation in
    Phase 34 keys off it. Stable across equal documents so two parallel
    builds for the same user collapse to one cache entry.
    """

    skills: list[ResolvedSkill]
    instructions: list[ResolvedInstruction]
    commands: list[ResolvedCommand]
    mcp_servers: list[ResolvedMcpServer]
    etag: str

    @classmethod
    def build(
        cls,
        *,
        skills: list[ResolvedSkill],
        instructions: list[ResolvedInstruction],
        commands: list[ResolvedCommand],
        mcp_servers: list[ResolvedMcpServer],
    ) -> EffectiveConfig:
        return cls(
            skills=skills,
            instructions=instructions,
            commands=commands,
            mcp_servers=mcp_servers,
            etag=_compute_etag(skills, instructions, commands, mcp_servers),
        )

    def to_json(self) -> str:
        """Return a Redis-cacheable JSON string of the resolved config.

        ``ResolvedMcpServer.auth_value_ct`` is base64-encoded so the round-trip
        is byte-exact; everything else is plain JSON. Use ``from_json`` to
        rebuild the dataclass tree.
        """
        return json.dumps(
            {
                "skills": [_skill_to_dict(s) for s in self.skills],
                "instructions": [_instruction_to_dict(i) for i in self.instructions],
                "commands": [_command_to_dict(c) for c in self.commands],
                "mcp_servers": [_mcp_to_dict(m) for m in self.mcp_servers],
                "etag": self.etag,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, blob: str) -> EffectiveConfig:
        data = json.loads(blob)
        return cls(
            skills=[_skill_from_dict(s) for s in data["skills"]],
            instructions=[_instruction_from_dict(i) for i in data["instructions"]],
            commands=[_command_from_dict(c) for c in data["commands"]],
            mcp_servers=[_mcp_from_dict(m) for m in data["mcp_servers"]],
            etag=data["etag"],
        )


def _skill_to_dict(s: ResolvedSkill) -> dict[str, Any]:
    return {
        "slug": s.slug,
        "scope": s.scope,
        "description": s.description,
        "body": s.body,
        "frontmatter": s.frontmatter,
        "enabled_default": s.enabled_default,
        "mandatory": s.mandatory,
    }


def _skill_from_dict(d: dict[str, Any]) -> ResolvedSkill:
    return ResolvedSkill(
        slug=d["slug"],
        scope=d["scope"],
        description=d["description"],
        body=d["body"],
        frontmatter=d["frontmatter"],
        enabled_default=d["enabled_default"],
        mandatory=d["mandatory"],
    )


def _instruction_to_dict(i: ResolvedInstruction) -> dict[str, Any]:
    return {
        "slug": i.slug,
        "scope": i.scope,
        "body": i.body,
        "priority": i.priority,
        "mandatory": i.mandatory,
    }


def _instruction_from_dict(d: dict[str, Any]) -> ResolvedInstruction:
    return ResolvedInstruction(
        slug=d["slug"],
        scope=d["scope"],
        body=d["body"],
        priority=d["priority"],
        mandatory=d["mandatory"],
    )


def _command_to_dict(c: ResolvedCommand) -> dict[str, Any]:
    return {
        "slug": c.slug,
        "scope": c.scope,
        "prompt_tpl": c.prompt_tpl,
        "description": c.description,
        "arg_schema": c.arg_schema,
        "bind_skills": list(c.bind_skills),
    }


def _command_from_dict(d: dict[str, Any]) -> ResolvedCommand:
    return ResolvedCommand(
        slug=d["slug"],
        scope=d["scope"],
        prompt_tpl=d["prompt_tpl"],
        description=d.get("description"),
        arg_schema=d.get("arg_schema"),
        bind_skills=list(d.get("bind_skills") or []),
    )


def _mcp_to_dict(m: ResolvedMcpServer) -> dict[str, Any]:
    return {
        "slug": m.slug,
        "scope": m.scope,
        "url": m.url,
        "auth_header": m.auth_header,
        "auth_value_ct_b64": (
            base64.b64encode(m.auth_value_ct).decode("ascii")
            if m.auth_value_ct is not None
            else None
        ),
        "cache_tools_list": m.cache_tools_list,
        "enabled": m.enabled,
        "mandatory": m.mandatory,
        "user_id": m.user_id,
    }


def _mcp_from_dict(d: dict[str, Any]) -> ResolvedMcpServer:
    raw = d.get("auth_value_ct_b64")
    return ResolvedMcpServer(
        slug=d["slug"],
        scope=d["scope"],
        url=d["url"],
        auth_header=d.get("auth_header"),
        auth_value_ct=base64.b64decode(raw) if raw else None,
        cache_tools_list=d["cache_tools_list"],
        enabled=d["enabled"],
        mandatory=d["mandatory"],
        user_id=d.get("user_id"),
    )


def _compute_etag(
    skills: list[ResolvedSkill],
    instructions: list[ResolvedInstruction],
    commands: list[ResolvedCommand],
    mcp_servers: list[ResolvedMcpServer],
) -> str:
    payload: dict[str, Any] = {
        "skills": [
            {"slug": s.slug, "scope": s.scope, "body": s.body} for s in skills
        ],
        "instructions": [
            {
                "slug": i.slug,
                "scope": i.scope,
                "priority": i.priority,
                "body": i.body,
                "mandatory": i.mandatory,
            }
            for i in instructions
        ],
        "commands": [
            {"slug": c.slug, "scope": c.scope, "prompt_tpl": c.prompt_tpl}
            for c in commands
        ],
        "mcp_servers": [
            {
                "slug": m.slug,
                "scope": m.scope,
                "url": m.url,
                "auth_header": m.auth_header,
                "enabled": m.enabled,
            }
            for m in mcp_servers
        ],
    }
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]
