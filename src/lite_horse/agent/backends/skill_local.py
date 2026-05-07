"""Local-FS :class:`SkillBackend` impl — wraps the v0.4 skills tree.

State lives at ``~/.litehorse/skills/<slug>/``. Writes go through the
existing :func:`dispatch` helper from :mod:`skills.local_dispatch` and
``view`` reuses :func:`_view` from :mod:`skills.local_view`; the stats
sidecar is updated through :mod:`skills.stats` against the resolved
skill directory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from lite_horse.agent.backends.skill import SkillBackend
from lite_horse.effective import ResolvedSkill
from lite_horse.skills import stats as skill_stats
from lite_horse.skills._slug import _SLUG_RE
from lite_horse.skills.local_dispatch import dispatch as _dispatch
from lite_horse.skills.local_view import _view as _local_view
from lite_horse.skills.source import skills_root


def _skill_dir_safe(slug: str) -> Path | None:
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        return None
    root = skills_root().resolve()
    target = (root / slug).resolve()
    if not target.is_relative_to(root):
        return None
    return target

_FRAGILE_MIN_ERRORS = 3
_FRAGILE_MAX_SUCCESS_RATIO = 0.5
_FRAGILE_TAG = " (fragile — see stats)"


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].lstrip("\n")
    body = text[end + 4 :].lstrip("\n")
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}, body
    if not isinstance(data, dict):
        return {}, body
    return data, body


class SkillLocalBackend(SkillBackend):
    """Filesystem-backed skill store rooted at ``skills_root()``."""

    async def list_slugs(self) -> list[str]:
        root = skills_root()
        return sorted(p.name for p in root.iterdir() if p.is_dir())

    async def list_resolved(self) -> list[ResolvedSkill]:
        out: list[ResolvedSkill] = []
        root = skills_root()
        if not root.is_dir():
            return out
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            md = child / "SKILL.md"
            if not md.is_file():
                continue
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            fm, body = _parse_frontmatter(text)
            description = str(fm.get("description") or "").strip()
            out.append(
                ResolvedSkill(
                    slug=child.name,
                    scope="user",
                    description=description,
                    body=body,
                    frontmatter=dict(fm),
                    enabled_default=True,
                    mandatory=False,
                )
            )
        return out

    async def view(self, slug: str) -> dict[str, Any]:
        return _local_view(slug)

    async def read_md(self, slug: str) -> str | None:
        path = skills_root() / slug / "SKILL.md"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    async def create(self, *, slug: str, content: str) -> dict[str, Any]:
        return _dispatch("create", name=slug, content=content)

    async def patch(
        self, *, slug: str, old_string: str, new_string: str
    ) -> dict[str, Any]:
        return _dispatch(
            "patch", name=slug, old_string=old_string, new_string=new_string
        )

    async def edit(self, *, slug: str, content: str) -> dict[str, Any]:
        return _dispatch("edit", name=slug, content=content)

    async def delete(self, *, slug: str) -> dict[str, Any]:
        return _dispatch("delete", name=slug)

    async def write_file(
        self, *, slug: str, file_path: str, content: str
    ) -> dict[str, Any]:
        return _dispatch(
            "write_file", name=slug, file_path=file_path, content=content
        )

    async def remove_file(
        self, *, slug: str, file_path: str
    ) -> dict[str, Any]:
        return _dispatch("remove_file", name=slug, file_path=file_path)

    async def record_view(self, slug: str) -> None:
        d = _skill_dir_safe(slug)
        if d is not None:
            skill_stats.record_view(d)

    async def record_outcome(
        self, slug: str, *, ok: bool, error_summary: str | None = None
    ) -> None:
        d = _skill_dir_safe(slug)
        if d is not None:
            skill_stats.record_outcome(d, ok=ok, error_summary=error_summary)

    async def fragile_suffix(self, slug: str) -> str:
        d = _skill_dir_safe(slug)
        data = skill_stats.read(d) if d is not None else None
        if not data:
            return ""
        errors = int(data.get("error_count", 0) or 0)
        successes = int(data.get("success_count", 0) or 0)
        uses = int(data.get("usage_count", 0) or 0)
        if errors < _FRAGILE_MIN_ERRORS or uses <= 0:
            return ""
        if successes / max(1, uses) >= _FRAGILE_MAX_SUCCESS_RATIO:
            return ""
        return _FRAGILE_TAG
