"""Layered cron-job repository — user-scope CRUD + scope-agnostic read.

Same shape as ``skill_repo`` / ``instruction_repo``. The scheduler
(Phase 36) is the consumer of the resolved cron list; this repo only
owns persistence.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, select, update

from lite_horse.models.cron_job import CronJob
from lite_horse.repositories.base import BaseRepo


class CronRepo(BaseRepo):
    """cron_jobs table CRUD."""

    # ---------- read ----------

    async def list_user(self) -> list[CronJob]:
        user_id = UUID(await self.current_user_id())
        stmt = (
            select(CronJob)
            .where(
                and_(CronJob.scope == "user", CronJob.user_id == user_id)
            )
            .order_by(CronJob.slug)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_official(self) -> list[CronJob]:
        stmt = (
            select(CronJob)
            .where(CronJob.scope == "official")
            .order_by(CronJob.slug)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_user(self, slug: str) -> CronJob | None:
        user_id = UUID(await self.current_user_id())
        stmt = select(CronJob).where(
            and_(
                CronJob.scope == "user",
                CronJob.user_id == user_id,
                CronJob.slug == slug,
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_official(self, slug: str) -> CronJob | None:
        stmt = select(CronJob).where(
            and_(CronJob.scope == "official", CronJob.slug == slug)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ---------- write (user scope only) ----------

    async def create_user(
        self,
        *,
        slug: str,
        cron_expr: str,
        prompt: str,
        webhook_url: str | None = None,
        enabled: bool = True,
    ) -> CronJob:
        user_id = UUID(await self.current_user_id())
        row = CronJob(
            id=uuid4(),
            scope="user",
            user_id=user_id,
            slug=slug,
            cron_expr=cron_expr,
            prompt=prompt,
            webhook_url=webhook_url,
            enabled=enabled,
            mandatory=False,
            strikes=0,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_user(
        self,
        slug: str,
        *,
        cron_expr: str | None = None,
        prompt: str | None = None,
        webhook_url: str | None = None,
        clear_webhook_url: bool = False,
        enabled: bool | None = None,
    ) -> CronJob | None:
        user_id = UUID(await self.current_user_id())
        values: dict[str, Any] = {}
        if cron_expr is not None:
            values["cron_expr"] = cron_expr
        if prompt is not None:
            values["prompt"] = prompt
        if webhook_url is not None:
            values["webhook_url"] = webhook_url
        elif clear_webhook_url:
            values["webhook_url"] = None
        if enabled is not None:
            values["enabled"] = enabled
        if not values:
            return await self.get_user(slug)
        stmt = (
            update(CronJob)
            .where(
                and_(
                    CronJob.scope == "user",
                    CronJob.user_id == user_id,
                    CronJob.slug == slug,
                )
            )
            .values(**values)
            .returning(CronJob)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def delete_user(self, slug: str) -> bool:
        user_id = UUID(await self.current_user_id())
        stmt = (
            delete(CronJob)
            .where(
                and_(
                    CronJob.scope == "user",
                    CronJob.user_id == user_id,
                    CronJob.slug == slug,
                )
            )
            .returning(CronJob.id)
        )
        result = (await self.session.execute(stmt)).first()
        return result is not None

    # ---------- write (official scope, mutable in place) ----------

    async def create_official(
        self,
        *,
        slug: str,
        cron_expr: str,
        prompt: str,
        webhook_url: str | None = None,
        enabled: bool = True,
        mandatory: bool = False,
    ) -> CronJob:
        row = CronJob(
            id=uuid4(),
            scope="official",
            user_id=None,
            slug=slug,
            cron_expr=cron_expr,
            prompt=prompt,
            webhook_url=webhook_url,
            enabled=enabled,
            mandatory=mandatory,
            strikes=0,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_official(
        self,
        slug: str,
        *,
        cron_expr: str | None = None,
        prompt: str | None = None,
        webhook_url: str | None = None,
        clear_webhook_url: bool = False,
        enabled: bool | None = None,
        mandatory: bool | None = None,
    ) -> CronJob | None:
        values: dict[str, Any] = {}
        if cron_expr is not None:
            values["cron_expr"] = cron_expr
        if prompt is not None:
            values["prompt"] = prompt
        if webhook_url is not None:
            values["webhook_url"] = webhook_url
        elif clear_webhook_url:
            values["webhook_url"] = None
        if enabled is not None:
            values["enabled"] = enabled
        if mandatory is not None:
            values["mandatory"] = mandatory
        if not values:
            return await self.get_official(slug)
        stmt = (
            update(CronJob)
            .where(and_(CronJob.scope == "official", CronJob.slug == slug))
            .values(**values)
            .returning(CronJob)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def delete_official(self, slug: str) -> bool:
        stmt = (
            delete(CronJob)
            .where(and_(CronJob.scope == "official", CronJob.slug == slug))
            .returning(CronJob.id)
        )
        return (await self.session.execute(stmt)).first() is not None

    async def mark_fired(
        self, slug: str, *, when: datetime, reset_strikes: bool = True
    ) -> None:
        """Stamp ``last_fired_at`` after the scheduler delivers a turn."""
        user_id = UUID(await self.current_user_id())
        values: dict[str, Any] = {"last_fired_at": when}
        if reset_strikes:
            values["strikes"] = 0
        stmt = (
            update(CronJob)
            .where(
                and_(
                    CronJob.scope == "user",
                    CronJob.user_id == user_id,
                    CronJob.slug == slug,
                )
            )
            .values(**values)
        )
        await self.session.execute(stmt)
