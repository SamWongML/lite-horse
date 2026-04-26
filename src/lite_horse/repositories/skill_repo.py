"""Layered skills repository — user-scope CRUD + ``list_effective`` resolver.

User-scope rows are mutable in place — no version bump on edit. Official
rows are versioned, but only Phase 34's admin layer writes them; here we
expose them read-only so the resolver can see what's there.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, select, update

from lite_horse.bundled.loaders import BundledSkill, load_bundled_skills
from lite_horse.effective import ResolvedSkill
from lite_horse.models.skill import Skill
from lite_horse.repositories.base import BaseRepo


class SkillRepo(BaseRepo):
    """skills table CRUD. One instance per ``AsyncSession``."""

    # ---------- read (both scopes) ----------

    async def list_user(self) -> list[Skill]:
        user_id = UUID(await self.current_user_id())
        stmt = (
            select(Skill)
            .where(
                and_(
                    Skill.scope == "user",
                    Skill.user_id == user_id,
                    Skill.is_current.is_(True),
                )
            )
            .order_by(Skill.slug)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_official(self) -> list[Skill]:
        stmt = (
            select(Skill)
            .where(and_(Skill.scope == "official", Skill.is_current.is_(True)))
            .order_by(Skill.slug)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_user(self, slug: str) -> Skill | None:
        user_id = UUID(await self.current_user_id())
        stmt = select(Skill).where(
            and_(
                Skill.scope == "user",
                Skill.user_id == user_id,
                Skill.slug == slug,
                Skill.is_current.is_(True),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_official(self, slug: str) -> Skill | None:
        stmt = select(Skill).where(
            and_(
                Skill.scope == "official",
                Skill.slug == slug,
                Skill.is_current.is_(True),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ---------- write (user scope only — official lands in Phase 34) ----------

    async def create_user(
        self,
        *,
        slug: str,
        frontmatter: dict[str, Any],
        body: str,
        enabled_default: bool = True,
    ) -> Skill:
        user_id = UUID(await self.current_user_id())
        row = Skill(
            id=uuid4(),
            scope="user",
            user_id=user_id,
            slug=slug,
            version=1,
            is_current=True,
            mandatory=False,
            enabled_default=enabled_default,
            frontmatter=frontmatter,
            body=body,
            created_by=user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_user(
        self,
        slug: str,
        *,
        frontmatter: dict[str, Any] | None = None,
        body: str | None = None,
        enabled_default: bool | None = None,
    ) -> Skill | None:
        user_id = UUID(await self.current_user_id())
        values: dict[str, Any] = {}
        if frontmatter is not None:
            values["frontmatter"] = frontmatter
        if body is not None:
            values["body"] = body
        if enabled_default is not None:
            values["enabled_default"] = enabled_default
        if not values:
            return await self.get_user(slug)
        stmt = (
            update(Skill)
            .where(
                and_(
                    Skill.scope == "user",
                    Skill.user_id == user_id,
                    Skill.slug == slug,
                    Skill.is_current.is_(True),
                )
            )
            .values(**values)
            .returning(Skill)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def delete_user(self, slug: str) -> bool:
        user_id = UUID(await self.current_user_id())
        stmt = (
            delete(Skill)
            .where(
                and_(
                    Skill.scope == "user",
                    Skill.user_id == user_id,
                    Skill.slug == slug,
                )
            )
            .returning(Skill.id)
        )
        result = (await self.session.execute(stmt)).first()
        return result is not None

    # ---------- layered resolution ----------

    async def list_effective(
        self, opt_out_slugs: Iterable[str] = ()
    ) -> list[ResolvedSkill]:
        """Return bundled + official + user skills under resolution rules.

        - Bundled rows are always included.
        - Mandatory officials always included; non-mandatory officials are
          dropped when their slug appears in ``opt_out_slugs``.
        - User-scope rows shadow officials only when the official is
          non-mandatory; mandatory officials cannot be shadowed.
        - Bundled rows are shadowed by either official or user with the
          same slug (same precedence: bundled < official < user).
        """
        opt_outs = set(opt_out_slugs)
        officials = await self.list_official()
        users = await self.list_user()
        bundled = load_bundled_skills()

        mandatory_official_slugs = {
            o.slug for o in officials if o.mandatory
        }

        merged: dict[str, ResolvedSkill] = {}
        for b in bundled:
            merged[b.slug] = _bundled_to_resolved(b)

        for o in officials:
            if not o.mandatory and o.slug in opt_outs:
                continue
            merged[o.slug] = ResolvedSkill(
                slug=o.slug,
                scope="official",
                description=str(o.frontmatter.get("description") or "").strip(),
                body=o.body,
                frontmatter=dict(o.frontmatter),
                enabled_default=o.enabled_default,
                mandatory=o.mandatory,
            )

        for u in users:
            if u.slug in mandatory_official_slugs:
                # User-scope shadow of a mandatory official is silently
                # ignored — the mandatory rule is the whole point of
                # marking it mandatory.
                continue
            merged[u.slug] = ResolvedSkill(
                slug=u.slug,
                scope="user",
                description=str(u.frontmatter.get("description") or "").strip(),
                body=u.body,
                frontmatter=dict(u.frontmatter),
                enabled_default=u.enabled_default,
                mandatory=False,
            )

        return sorted(merged.values(), key=lambda s: s.slug)


def _bundled_to_resolved(b: BundledSkill) -> ResolvedSkill:
    return ResolvedSkill(
        slug=b.slug,
        scope="bundled",
        description=b.description,
        body=b.body,
        frontmatter=dict(b.frontmatter),
        enabled_default=True,
        mandatory=False,
    )
