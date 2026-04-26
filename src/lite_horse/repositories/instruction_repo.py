"""Layered instructions repository — user-scope CRUD + scope-agnostic read.

Same shape as ``skill_repo``: user rows mutable in place, official rows
read-only here (Phase 34 admin layer is the writer). Lists are ordered
by ``(priority ASC, scope DESC)`` so officials sort before user shadows
when priorities tie — this is the order the resolver consumes.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, select, update

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
