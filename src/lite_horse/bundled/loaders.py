"""Read bundled skills / instructions / commands from the package tree.

These are the in-image, read-only third scope of the layered config
(``bundled``). Everything ships with the application image; the resolver
folds them in alongside official (DB) and user (DB) rows so the agent
always has the baseline behaviours regardless of DB state.

Loaders are pure file-system reads — no caching, no I/O contention. The
resolver runs them once per ``compute_effective_config`` call; that's
microseconds per asset and not worth memoising.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_BUNDLED_ROOT = Path(__file__).parent
_SKILLS_DIR = _BUNDLED_ROOT / "skills"
_INSTRUCTIONS_DIR = _BUNDLED_ROOT / "instructions"
_COMMANDS_DIR = _BUNDLED_ROOT / "commands"


@dataclass(frozen=True)
class BundledSkill:
    slug: str
    frontmatter: dict[str, Any]
    body: str

    @property
    def description(self) -> str:
        return str(self.frontmatter.get("description") or "").strip()

    @property
    def activate_when(self) -> list[dict[str, list[str]]]:
        raw = self.frontmatter.get("activate_when")
        return _coerce_activation_rules(raw)


@dataclass(frozen=True)
class BundledInstruction:
    slug: str
    body: str
    priority: int = 100
    mandatory: bool = False


@dataclass(frozen=True)
class BundledCommand:
    slug: str
    prompt_tpl: str
    description: str | None = None
    arg_schema: dict[str, Any] | None = None
    bind_skills: list[str] = field(default_factory=list)


def load_bundled_skills() -> list[BundledSkill]:
    if not _SKILLS_DIR.is_dir():
        return []
    out: list[BundledSkill] = []
    for child in sorted(_SKILLS_DIR.iterdir()):
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        frontmatter, body = _split_frontmatter(skill_md.read_text(encoding="utf-8"))
        out.append(BundledSkill(slug=child.name, frontmatter=frontmatter, body=body))
    return out


def load_bundled_instructions() -> list[BundledInstruction]:
    if not _INSTRUCTIONS_DIR.is_dir():
        return []
    out: list[BundledInstruction] = []
    for child in sorted(_INSTRUCTIONS_DIR.iterdir()):
        if child.suffix != ".md" or not child.is_file():
            continue
        frontmatter, body = _split_frontmatter(child.read_text(encoding="utf-8"))
        slug = str(frontmatter.get("slug") or child.stem)
        priority = int(frontmatter.get("priority", 100))
        mandatory = bool(frontmatter.get("mandatory", False))
        out.append(
            BundledInstruction(
                slug=slug, body=body, priority=priority, mandatory=mandatory
            )
        )
    return out


def load_bundled_commands() -> list[BundledCommand]:
    if not _COMMANDS_DIR.is_dir():
        return []
    out: list[BundledCommand] = []
    for child in sorted(_COMMANDS_DIR.iterdir()):
        if child.suffix not in (".yaml", ".yml") or not child.is_file():
            continue
        data = yaml.safe_load(child.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        slug = str(data.get("slug") or child.stem)
        prompt_tpl = str(data.get("prompt_tpl") or "")
        description = data.get("description")
        arg_schema = data.get("arg_schema")
        bind_skills_raw = data.get("bind_skills") or []
        bind_skills = [str(b) for b in bind_skills_raw if isinstance(b, str)]
        out.append(
            BundledCommand(
                slug=slug,
                prompt_tpl=prompt_tpl,
                description=str(description).strip() if description else None,
                arg_schema=arg_schema if isinstance(arg_schema, dict) else None,
                bind_skills=bind_skills,
            )
        )
    return out


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return ``(frontmatter, body)``. Empty frontmatter when absent."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].lstrip("\n")
    body = text[end + 4 :].lstrip("\n")
    try:
        data = yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, body
    return data, body


def _coerce_activation_rules(raw: Any) -> list[dict[str, list[str]]]:
    if not isinstance(raw, list):
        return []
    rules: list[dict[str, list[str]]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rule: dict[str, list[str]] = {}
        kws = item.get("keywords")
        if isinstance(kws, list):
            rule["keywords"] = [
                str(k) for k in kws if isinstance(k, str) and k.strip()
            ]
        globs = item.get("file_globs")
        if isinstance(globs, list):
            rule["file_globs"] = [
                str(g) for g in globs if isinstance(g, str) and g.strip()
            ]
        if rule:
            rules.append(rule)
    return rules
