"""Cloud :class:`SkillBackend` impl — wraps :class:`SkillRepo`.

User-scope skills only — official-scope skills come through the resolver
(``EffectiveConfig.skills``) and are read-only from the agent's
perspective. The cloud impl does not maintain a per-skill stats sidecar:
Phase 44 lifts that into the ``skills`` table via curator columns. Until
then ``record_view`` / ``record_outcome`` / ``fragile_suffix`` are
no-ops in cloud mode, which matches the v0.4 behaviour where there was
no DB-backed sidecar at all.
"""
from __future__ import annotations

from typing import Any

import yaml

from lite_horse.agent.backends.skill import SkillBackend
from lite_horse.effective import EffectiveConfig, ResolvedSkill
from lite_horse.repositories.skill_repo import SkillRepo
from lite_horse.security.validators import UnsafeContent, check_untrusted
from lite_horse.skills._slug import _SLUG_RE
from lite_horse.storage.db import db_session

_SKILL_VIEW_MAX_BYTES = 16 * 1024
_TRUNCATION_MARKER = "\n\n[... truncated by skill_view; full file on disk]"


def _parse_md(content: str) -> tuple[dict[str, Any], str] | None:
    if not content.startswith("---"):
        return None
    end = content.find("\n---", 3)
    if end == -1:
        return None
    block = content[3:end].lstrip("\n")
    body = content[end + 4 :].lstrip("\n")
    try:
        fm = yaml.safe_load(block)
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    return fm, body


def _serialise_md(frontmatter: dict[str, Any], body: str) -> str:
    """Render a SKILL.md from frontmatter+body, matching the local format."""
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    return f"---\n{fm_yaml}\n---\n\n{body}".rstrip() + "\n"


class SkillCloudBackend(SkillBackend):
    """Postgres-backed user-scope skill store for one user.

    The provided :class:`EffectiveConfig` snapshot is the source of truth
    for ``list_resolved`` so the prompt index includes bundled / official
    rows alongside user-scope writes; mutations go through
    :class:`SkillRepo` against ``user`` scope only.
    """

    def __init__(
        self, *, user_id: str, effective: EffectiveConfig | None = None
    ) -> None:
        self.user_id = user_id
        self._effective = effective

    async def list_slugs(self) -> list[str]:
        async with db_session(self.user_id) as session:
            rows = await SkillRepo(session).list_user()
        return sorted(r.slug for r in rows)

    async def list_resolved(self) -> list[ResolvedSkill]:
        if self._effective is not None:
            return list(self._effective.skills)
        async with db_session(self.user_id) as session:
            return await SkillRepo(session).list_effective()

    async def view(self, slug: str) -> dict[str, Any]:
        if not isinstance(slug, str) or not _SLUG_RE.match(slug):
            return {
                "success": False,
                "error": (
                    f"invalid skill name {slug!r}; must be lowercase, "
                    "alphanumeric + dash/underscore, max 64 chars, "
                    "start with [a-z0-9]"
                ),
            }
        text = await self.read_md(slug)
        if text is None:
            return {"success": False, "error": f"skill {slug!r} not found"}
        if len(text.encode("utf-8")) > _SKILL_VIEW_MAX_BYTES:
            encoded = text.encode("utf-8")[
                : _SKILL_VIEW_MAX_BYTES - len(_TRUNCATION_MARKER)
            ]
            text = encoded.decode("utf-8", errors="ignore") + _TRUNCATION_MARKER
        await self.record_view(slug)
        return {"success": True, "name": slug, "content": text}

    async def read_md(self, slug: str) -> str | None:
        if self._effective is not None:
            for s in self._effective.skills:
                if s.slug == slug:
                    return _serialise_md(dict(s.frontmatter), s.body)
        async with db_session(self.user_id) as session:
            repo = SkillRepo(session)
            row = await repo.get_user(slug)
            if row is None:
                row = await repo.get_official(slug)
            if row is None:
                return None
            return _serialise_md(dict(row.frontmatter), row.body)

    async def create(self, *, slug: str, content: str) -> dict[str, Any]:
        if not _SLUG_RE.match(slug):
            return {
                "success": False,
                "error": (
                    f"invalid skill name {slug!r}; must be lowercase, "
                    "alphanumeric + dash/underscore, max 64 chars, "
                    "start with [a-z0-9]"
                ),
            }
        parsed = _parse_md(content)
        if parsed is None:
            return {
                "success": False,
                "error": (
                    "content must be a complete SKILL.md with YAML frontmatter "
                    "(--- name: ... description: ... ---)"
                ),
            }
        try:
            check_untrusted(content)
        except UnsafeContent as e:
            return {"success": False, "error": f"unsafe skill content: {e}"}
        frontmatter, body = parsed
        async with db_session(self.user_id) as session:
            repo = SkillRepo(session)
            existing = await repo.get_user(slug)
            if existing is not None:
                return {
                    "success": False,
                    "error": f"skill {slug!r} already exists",
                }
            await repo.create_user(slug=slug, frontmatter=frontmatter, body=body)
        return {"success": True, "path": f"skills/{slug}/SKILL.md"}

    async def patch(
        self, *, slug: str, old_string: str, new_string: str
    ) -> dict[str, Any]:
        async with db_session(self.user_id) as session:
            repo = SkillRepo(session)
            row = await repo.get_user(slug)
            if row is None:
                return {
                    "success": False,
                    "error": f"skill {slug!r} does not exist",
                }
            text = _serialise_md(dict(row.frontmatter), row.body)
            count = text.count(old_string)
            if count == 0:
                return {"success": False, "error": "old_string not found"}
            if count > 1:
                return {
                    "success": False,
                    "error": f"old_string matches {count} times; make it unique",
                }
            new_text = text.replace(old_string, new_string, 1)
            parsed = _parse_md(new_text)
            if parsed is None:
                return {
                    "success": False,
                    "error": "patch broke the YAML frontmatter",
                }
            try:
                check_untrusted(new_text)
            except UnsafeContent as e:
                return {
                    "success": False,
                    "error": f"unsafe skill content: {e}",
                }
            new_fm, new_body = parsed
            await repo.update_user(
                slug, frontmatter=new_fm, body=new_body
            )
        return {"success": True}

    async def edit(self, *, slug: str, content: str) -> dict[str, Any]:
        parsed = _parse_md(content)
        if parsed is None:
            return {
                "success": False,
                "error": "content must include YAML frontmatter",
            }
        try:
            check_untrusted(content)
        except UnsafeContent as e:
            return {"success": False, "error": f"unsafe skill content: {e}"}
        frontmatter, body = parsed
        async with db_session(self.user_id) as session:
            repo = SkillRepo(session)
            row = await repo.get_user(slug)
            if row is None:
                return {
                    "success": False,
                    "error": f"skill {slug!r} does not exist",
                }
            await repo.update_user(slug, frontmatter=frontmatter, body=body)
        return {"success": True}

    async def delete(self, *, slug: str) -> dict[str, Any]:
        async with db_session(self.user_id) as session:
            ok = await SkillRepo(session).delete_user(slug)
        if not ok:
            return {"success": False, "error": f"skill {slug!r} does not exist"}
        return {"success": True}

    async def write_file(
        self, *, slug: str, file_path: str, content: str
    ) -> dict[str, Any]:
        del slug, file_path, content
        return {
            "success": False,
            "error": (
                "write_file is not supported in cloud mode; supporting "
                "files lands in a future phase"
            ),
        }

    async def remove_file(
        self, *, slug: str, file_path: str
    ) -> dict[str, Any]:
        del slug, file_path
        return {
            "success": False,
            "error": (
                "remove_file is not supported in cloud mode; supporting "
                "files lands in a future phase"
            ),
        }

    async def record_view(self, slug: str) -> None:
        del slug

    async def record_outcome(
        self, slug: str, *, ok: bool, error_summary: str | None = None
    ) -> None:
        del slug, ok, error_summary

    async def fragile_suffix(self, slug: str) -> str:
        del slug
        return ""
