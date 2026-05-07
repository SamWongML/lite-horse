"""``SkillBackend`` Protocol — tenant-safe skill CRUD + stats.

Tool bodies (``skill_manage``, ``skill_view``) and hooks
(``EvolutionHook``) ask ``ctx.context.skill`` for skill operations and never
reach into ``skills_root()`` or :mod:`lite_horse.skills.stats` directly.

Cloud impl talks to :class:`SkillRepo` over a per-call short-lived
transaction; local impl wraps the v0.4 FS code under
``~/.litehorse/skills/``.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from lite_horse.effective import ResolvedSkill


@runtime_checkable
class SkillBackend(Protocol):
    """Per-tenant skill repository.

    Each method that mutates state is paired with the skill_manage tool
    action of the same name. Read paths (``list_resolved`` / ``view`` /
    ``read_md``) feed the prompt-index builder + the EvolutionHook
    refiner.
    """

    async def list_slugs(self) -> list[str]:
        """Return slug list (used by skill_manage action='list')."""
        ...

    async def list_resolved(self) -> list[ResolvedSkill]:
        """Return resolved skill rows for the prompt index + activation."""
        ...

    async def view(self, slug: str) -> dict[str, Any]:
        """Read SKILL.md body. Mirrors the wire shape of skill_view._view."""
        ...

    async def read_md(self, slug: str) -> str | None:
        """Return raw SKILL.md text or None if missing (used by EvolutionHook)."""
        ...

    async def create(self, *, slug: str, content: str) -> dict[str, Any]:
        """Create a new skill from a full SKILL.md string with frontmatter."""
        ...

    async def patch(
        self, *, slug: str, old_string: str, new_string: str
    ) -> dict[str, Any]:
        """Targeted old_string→new_string edit on SKILL.md."""
        ...

    async def edit(self, *, slug: str, content: str) -> dict[str, Any]:
        """Full SKILL.md rewrite."""
        ...

    async def delete(self, *, slug: str) -> dict[str, Any]:
        """Remove a skill."""
        ...

    async def write_file(
        self, *, slug: str, file_path: str, content: str
    ) -> dict[str, Any]:
        """Write a supporting file inside the skill dir."""
        ...

    async def remove_file(self, *, slug: str, file_path: str) -> dict[str, Any]:
        """Remove a supporting file."""
        ...

    async def record_view(self, slug: str) -> None:
        """Bump usage_count + last_used_at on skill_view success."""
        ...

    async def record_outcome(
        self, slug: str, *, ok: bool, error_summary: str | None = None
    ) -> None:
        """Bump success/error counters on run end."""
        ...

    async def fragile_suffix(self, slug: str) -> str:
        """Return ``" (fragile — see stats)"`` when the skill is decay-tagged."""
        ...
