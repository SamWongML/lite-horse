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
