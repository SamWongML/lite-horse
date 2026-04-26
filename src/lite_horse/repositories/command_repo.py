"""Layered commands repository — slash-command templates with sandboxed render.

User-scope rows are mutable in place; official rows are read-only here
(Phase 34 admin layer writes them). ``expand(slug, args)`` renders the
``prompt_tpl`` against ``args`` through a ``SandboxedEnvironment`` —
arbitrary attribute traversal, ``__class__`` access, and IO are blocked
so a malicious template body or arg cannot escape the prompt.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID, uuid4

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import and_, delete, select, update

from lite_horse.bundled.loaders import load_bundled_commands
from lite_horse.effective import ResolvedCommand
from lite_horse.models.command import Command
from lite_horse.repositories.base import BaseRepo

_ENV = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)


class CommandRepo(BaseRepo):
    """commands table CRUD + sandboxed template render."""

    # ---------- read ----------

    async def list_user(self) -> list[Command]:
        user_id = UUID(await self.current_user_id())
        stmt = (
            select(Command)
            .where(
                and_(
                    Command.scope == "user",
                    Command.user_id == user_id,
                    Command.is_current.is_(True),
                )
            )
            .order_by(Command.slug)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_official(self) -> list[Command]:
        stmt = (
            select(Command)
            .where(and_(Command.scope == "official", Command.is_current.is_(True)))
            .order_by(Command.slug)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_user(self, slug: str) -> Command | None:
        user_id = UUID(await self.current_user_id())
        stmt = select(Command).where(
            and_(
                Command.scope == "user",
                Command.user_id == user_id,
                Command.slug == slug,
                Command.is_current.is_(True),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_official(self, slug: str) -> Command | None:
        stmt = select(Command).where(
            and_(
                Command.scope == "official",
                Command.slug == slug,
                Command.is_current.is_(True),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ---------- write (user scope only) ----------

    async def create_user(
        self,
        *,
        slug: str,
        prompt_tpl: str,
        description: str | None = None,
        arg_schema: dict[str, Any] | None = None,
        bind_skills: list[str] | None = None,
    ) -> Command:
        user_id = UUID(await self.current_user_id())
        row = Command(
            id=uuid4(),
            scope="user",
            user_id=user_id,
            slug=slug,
            version=1,
            is_current=True,
            mandatory=False,
            description=description,
            prompt_tpl=prompt_tpl,
            arg_schema=arg_schema,
            bind_skills=bind_skills,
            created_by=user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_user(
        self,
        slug: str,
        *,
        prompt_tpl: str | None = None,
        description: str | None = None,
        arg_schema: dict[str, Any] | None = None,
        bind_skills: list[str] | None = None,
    ) -> Command | None:
        user_id = UUID(await self.current_user_id())
        values: dict[str, Any] = {}
        if prompt_tpl is not None:
            values["prompt_tpl"] = prompt_tpl
        if description is not None:
            values["description"] = description
        if arg_schema is not None:
            values["arg_schema"] = arg_schema
        if bind_skills is not None:
            values["bind_skills"] = bind_skills
        if not values:
            return await self.get_user(slug)
        stmt = (
            update(Command)
            .where(
                and_(
                    Command.scope == "user",
                    Command.user_id == user_id,
                    Command.slug == slug,
                    Command.is_current.is_(True),
                )
            )
            .values(**values)
            .returning(Command)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def delete_user(self, slug: str) -> bool:
        user_id = UUID(await self.current_user_id())
        stmt = (
            delete(Command)
            .where(
                and_(
                    Command.scope == "user",
                    Command.user_id == user_id,
                    Command.slug == slug,
                )
            )
            .returning(Command.id)
        )
        result = (await self.session.execute(stmt)).first()
        return result is not None

    # ---------- expand ----------

    async def expand(self, slug: str, args: dict[str, Any]) -> str:
        """Render ``prompt_tpl`` with ``args`` through a sandboxed Jinja env.

        Resolution order matches the layered config rules: user-scope row
        wins over official. Returns the rendered prompt; raises if the
        slug is unknown or the template references a missing variable.
        """
        row = await self.get_user(slug)
        if row is None:
            row = await self.get_official(slug)
        if row is None:
            # Fall back to bundled.
            for bundled in load_bundled_commands():
                if bundled.slug == slug:
                    return _ENV.from_string(bundled.prompt_tpl).render(**args)
            raise KeyError(f"unknown command slug: {slug!r}")
        template = _ENV.from_string(row.prompt_tpl)
        return template.render(**args)

    # ---------- layered resolution ----------

    async def list_effective(
        self, opt_out_slugs: Iterable[str] = ()
    ) -> list[ResolvedCommand]:
        """Return bundled + official + user commands, resolved."""
        opt_outs = set(opt_out_slugs)
        officials = await self.list_official()
        users = await self.list_user()
        bundled = load_bundled_commands()

        mandatory_official_slugs = {o.slug for o in officials if o.mandatory}

        merged: dict[str, ResolvedCommand] = {}
        for b in bundled:
            merged[b.slug] = ResolvedCommand(
                slug=b.slug,
                scope="bundled",
                prompt_tpl=b.prompt_tpl,
                description=b.description,
                arg_schema=b.arg_schema,
                bind_skills=list(b.bind_skills),
            )
        for o in officials:
            if not o.mandatory and o.slug in opt_outs:
                continue
            merged[o.slug] = ResolvedCommand(
                slug=o.slug,
                scope="official",
                prompt_tpl=o.prompt_tpl,
                description=o.description,
                arg_schema=o.arg_schema,
                bind_skills=list(o.bind_skills or []),
            )
        for u in users:
            if u.slug in mandatory_official_slugs:
                continue
            merged[u.slug] = ResolvedCommand(
                slug=u.slug,
                scope="user",
                prompt_tpl=u.prompt_tpl,
                description=u.description,
                arg_schema=u.arg_schema,
                bind_skills=list(u.bind_skills or []),
            )
        return sorted(merged.values(), key=lambda c: c.slug)
