"""Per-user opt-outs against non-mandatory official entities.

Mandatory officials cannot be opted out (the route layer rejects with
422 in Phase 34); this repo holds only the storage primitives.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from lite_horse.models.opt_out import UserOfficialOptOut
from lite_horse.repositories.base import BaseRepo

VALID_ENTITIES = ("skill", "instruction", "command", "mcp_server", "cron_job")


class OptOutRepo(BaseRepo):
    """user_official_opt_outs CRUD."""

    async def list(
        self, entity: str | None = None
    ) -> list[tuple[str, str]]:
        """Return ``(entity, slug)`` pairs for the current user."""
        if entity is not None and entity not in VALID_ENTITIES:
            raise ValueError(f"unknown opt-out entity: {entity!r}")
        user_id = UUID(await self.current_user_id())
        stmt = select(
            UserOfficialOptOut.entity, UserOfficialOptOut.slug
        ).where(UserOfficialOptOut.user_id == user_id)
        if entity is not None:
            stmt = stmt.where(UserOfficialOptOut.entity == entity)
        rows = (await self.session.execute(stmt)).all()
        return [(str(r.entity), str(r.slug)) for r in rows]

    async def add(self, entity: str, slug: str) -> None:
        """Insert ``(user, entity, slug)``; idempotent on PK conflict."""
        if entity not in VALID_ENTITIES:
            raise ValueError(f"unknown opt-out entity: {entity!r}")
        user_id = UUID(await self.current_user_id())
        stmt = (
            pg_insert(UserOfficialOptOut)
            .values(user_id=user_id, entity=entity, slug=slug)
            .on_conflict_do_nothing(
                index_elements=[
                    UserOfficialOptOut.user_id,
                    UserOfficialOptOut.entity,
                    UserOfficialOptOut.slug,
                ]
            )
        )
        await self.session.execute(stmt)

    async def remove(self, entity: str, slug: str) -> bool:
        if entity not in VALID_ENTITIES:
            raise ValueError(f"unknown opt-out entity: {entity!r}")
        user_id = UUID(await self.current_user_id())
        stmt = (
            delete(UserOfficialOptOut)
            .where(
                and_(
                    UserOfficialOptOut.user_id == user_id,
                    UserOfficialOptOut.entity == entity,
                    UserOfficialOptOut.slug == slug,
                )
            )
            .returning(UserOfficialOptOut.slug)
        )
        return (await self.session.execute(stmt)).first() is not None
