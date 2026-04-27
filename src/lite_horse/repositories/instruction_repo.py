"""Layered instructions repository — user-scope CRUD + ``list_effective``.

User rows mutable in place, official rows read-only here (Phase 34 admin
layer is the writer). ``list_effective`` returns the resolved list,
ordered by ``(priority ASC, slug ASC)`` for stable system-prompt
composition.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, select, update

from lite_horse.bundled.loaders import load_bundled_instructions
from lite_horse.effective import ResolvedInstruction
from lite_horse.models.instruction import Instruction
from lite_horse.repositories.base import BaseRepo


class InstructionRepo(BaseRepo):
    """instructions table CRUD. One instance per ``AsyncSession``."""

    # ---------- read ----------

    async def list_user(self) -> list[Instruction]:
        user_id = UUID(await self.current_user_id())
        stmt = (
            select(Instruction)
            .where(
                and_(
                    Instruction.scope == "user",
                    Instruction.user_id == user_id,
                    Instruction.is_current.is_(True),
                )
            )
            .order_by(Instruction.priority, Instruction.slug)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_official(self) -> list[Instruction]:
        stmt = (
            select(Instruction)
            .where(
                and_(
                    Instruction.scope == "official",
                    Instruction.is_current.is_(True),
                )
            )
            .order_by(Instruction.priority, Instruction.slug)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_user(self, slug: str) -> Instruction | None:
        user_id = UUID(await self.current_user_id())
        stmt = select(Instruction).where(
            and_(
                Instruction.scope == "user",
                Instruction.user_id == user_id,
                Instruction.slug == slug,
                Instruction.is_current.is_(True),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_official(self, slug: str) -> Instruction | None:
        stmt = select(Instruction).where(
            and_(
                Instruction.scope == "official",
                Instruction.slug == slug,
                Instruction.is_current.is_(True),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ---------- write (user scope only) ----------

    async def create_user(
        self,
        *,
        slug: str,
        body: str,
        priority: int = 100,
    ) -> Instruction:
        user_id = UUID(await self.current_user_id())
        row = Instruction(
            id=uuid4(),
            scope="user",
            user_id=user_id,
            slug=slug,
            version=1,
            is_current=True,
            mandatory=False,
            priority=priority,
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
        body: str | None = None,
        priority: int | None = None,
    ) -> Instruction | None:
        user_id = UUID(await self.current_user_id())
        values: dict[str, Any] = {}
        if body is not None:
            values["body"] = body
        if priority is not None:
            values["priority"] = priority
        if not values:
            return await self.get_user(slug)
        stmt = (
            update(Instruction)
            .where(
                and_(
                    Instruction.scope == "user",
                    Instruction.user_id == user_id,
                    Instruction.slug == slug,
                    Instruction.is_current.is_(True),
                )
            )
            .values(**values)
            .returning(Instruction)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def delete_user(self, slug: str) -> bool:
        user_id = UUID(await self.current_user_id())
        stmt = (
            delete(Instruction)
            .where(
                and_(
                    Instruction.scope == "user",
                    Instruction.user_id == user_id,
                    Instruction.slug == slug,
                )
            )
            .returning(Instruction.id)
        )
        result = (await self.session.execute(stmt)).first()
        return result is not None

    # ---------- write (official scope) ----------

    async def create_official(
        self,
        *,
        slug: str,
        body: str,
        priority: int = 100,
        mandatory: bool = False,
        created_by: UUID | None = None,
    ) -> Instruction:
        row = Instruction(
            id=uuid4(),
            scope="official",
            user_id=None,
            slug=slug,
            version=1,
            is_current=True,
            mandatory=mandatory,
            priority=priority,
            body=body,
            created_by=created_by,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_official(
        self,
        slug: str,
        *,
        body: str | None = None,
        priority: int | None = None,
        mandatory: bool | None = None,
        created_by: UUID | None = None,
    ) -> Instruction | None:
        current = await self.get_official(slug)
        if current is None:
            return None
        await self.session.execute(
            update(Instruction)
            .where(Instruction.id == current.id)
            .values(is_current=False)
        )
        new_row = Instruction(
            id=uuid4(),
            scope="official",
            user_id=None,
            slug=slug,
            version=current.version + 1,
            is_current=True,
            mandatory=current.mandatory if mandatory is None else mandatory,
            priority=current.priority if priority is None else priority,
            body=current.body if body is None else body,
            created_by=created_by,
        )
        self.session.add(new_row)
        await self.session.flush()
        return new_row

    async def delete_official(self, slug: str) -> bool:
        current = await self.get_official(slug)
        if current is None:
            return False
        await self.session.execute(
            update(Instruction)
            .where(Instruction.id == current.id)
            .values(is_current=False)
        )
        await self.session.flush()
        return True

    async def list_versions_official(self, slug: str) -> list[Instruction]:
        stmt = (
            select(Instruction)
            .where(and_(Instruction.scope == "official", Instruction.slug == slug))
            .order_by(Instruction.version.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def rollback_official(
        self, slug: str, version: int
    ) -> Instruction | None:
        target = (
            await self.session.execute(
                select(Instruction).where(
                    and_(
                        Instruction.scope == "official",
                        Instruction.slug == slug,
                        Instruction.version == version,
                    )
                )
            )
        ).scalar_one_or_none()
        if target is None:
            return None
        await self.session.execute(
            update(Instruction)
            .where(
                and_(
                    Instruction.scope == "official",
                    Instruction.slug == slug,
                    Instruction.is_current.is_(True),
                )
            )
            .values(is_current=False)
        )
        await self.session.execute(
            update(Instruction)
            .where(Instruction.id == target.id)
            .values(is_current=True)
        )
        await self.session.flush()
        await self.session.refresh(target)
        return target

    # ---------- layered resolution ----------

    async def list_effective(
        self, opt_out_slugs: Iterable[str] = ()
    ) -> list[ResolvedInstruction]:
        """Return bundled + official + user instructions, resolved.

        Output is sorted by ``(priority, slug)`` so the prompt composer
        can append in order without re-sorting.
        """
        opt_outs = set(opt_out_slugs)
        officials = await self.list_official()
        users = await self.list_user()
        bundled = load_bundled_instructions()

        mandatory_official_slugs = {o.slug for o in officials if o.mandatory}

        merged: dict[str, ResolvedInstruction] = {}
        for b in bundled:
            merged[b.slug] = ResolvedInstruction(
                slug=b.slug,
                scope="bundled",
                body=b.body,
                priority=b.priority,
                mandatory=b.mandatory,
            )
        for o in officials:
            if not o.mandatory and o.slug in opt_outs:
                continue
            merged[o.slug] = ResolvedInstruction(
                slug=o.slug,
                scope="official",
                body=o.body,
                priority=o.priority,
                mandatory=o.mandatory,
            )
        for u in users:
            if u.slug in mandatory_official_slugs:
                continue
            merged[u.slug] = ResolvedInstruction(
                slug=u.slug,
                scope="user",
                body=u.body,
                priority=u.priority,
                mandatory=False,
            )
        return sorted(merged.values(), key=lambda i: (i.priority, i.slug))
