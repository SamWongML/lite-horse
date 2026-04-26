"""Layered skills repository — user-scope CRUD + scope-agnostic read.

Per Phase 33a scope: this lands the storage primitives. The layered
``list_effective(user_id)`` loader (which folds bundled+official+user
per the resolution rules) is built on top of these reads in Phase 33b.

User-scope rows are mutable in place — no version bump on edit. Official
rows are versioned, but only Phase 34's admin layer writes them; here we
expose them read-only so the resolver can see what's there.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, select, update

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
