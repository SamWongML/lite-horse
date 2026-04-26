"""Layered MCP-server repository — KMS-encrypted auth values at rest.

User-scope writes route the plaintext auth value through ``Kms.encrypt``
with ``EncryptionContext={"user_id": ...}``; only the ciphertext lands
on disk. The agent factory decrypts on demand when building per-user
MCP servers. HTTP GET handlers surface metadata only — never the
plaintext auth value.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, select, update

from lite_horse.effective import ResolvedMcpServer
from lite_horse.models.mcp_server import McpServer
from lite_horse.repositories.base import BaseRepo
from lite_horse.storage.kms import Kms


class McpRepo(BaseRepo):
    """mcp_servers table CRUD + KMS envelope on auth values."""

    # ---------- read ----------

    async def list_user(self) -> list[McpServer]:
        user_id = UUID(await self.current_user_id())
        stmt = (
            select(McpServer)
            .where(
                and_(
                    McpServer.scope == "user",
                    McpServer.user_id == user_id,
                    McpServer.is_current.is_(True),
                )
            )
            .order_by(McpServer.slug)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_official(self) -> list[McpServer]:
        stmt = (
            select(McpServer)
            .where(
                and_(McpServer.scope == "official", McpServer.is_current.is_(True))
            )
            .order_by(McpServer.slug)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_user(self, slug: str) -> McpServer | None:
        user_id = UUID(await self.current_user_id())
        stmt = select(McpServer).where(
            and_(
                McpServer.scope == "user",
                McpServer.user_id == user_id,
                McpServer.slug == slug,
                McpServer.is_current.is_(True),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_official(self, slug: str) -> McpServer | None:
        stmt = select(McpServer).where(
            and_(
                McpServer.scope == "official",
                McpServer.slug == slug,
                McpServer.is_current.is_(True),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ---------- decrypt ----------

    async def decrypt_auth_value(
        self, row: McpServer, kms: Kms
    ) -> str | None:
        """Return the plaintext auth header value, or None if unset."""
        if row.auth_value_ct is None:
            return None
        # Encryption context is bound to the row's owning user; for
        # official rows the context is the literal "official" sentinel
        # (those are encrypted with a server-side BYO context).
        owner = str(row.user_id) if row.user_id is not None else "official"
        plaintext = await kms.decrypt(
            bytes(row.auth_value_ct), {"user_id": owner}
        )
        return plaintext.decode("utf-8")

    # ---------- write (user scope only) ----------

    async def create_user(
        self,
        *,
        slug: str,
        url: str,
        kms: Kms,
        auth_header: str | None = None,
        auth_value: str | None = None,
        cache_tools_list: bool = True,
        enabled: bool = True,
    ) -> McpServer:
        user_id = UUID(await self.current_user_id())
        ct: bytes | None = None
        if auth_value is not None:
            ct = await kms.encrypt(
                auth_value.encode("utf-8"), {"user_id": str(user_id)}
            )
        row = McpServer(
            id=uuid4(),
            scope="user",
            user_id=user_id,
            slug=slug,
            url=url,
            auth_header=auth_header,
            auth_value_ct=ct,
            auth_value_dk=None,
            cache_tools_list=cache_tools_list,
            enabled=enabled,
            mandatory=False,
            version=1,
            is_current=True,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_user(
        self,
        slug: str,
        *,
        kms: Kms,
        url: str | None = None,
        auth_header: str | None = None,
        auth_value: str | None = None,
        clear_auth_value: bool = False,
        cache_tools_list: bool | None = None,
        enabled: bool | None = None,
    ) -> McpServer | None:
        user_id = UUID(await self.current_user_id())
        values: dict[str, Any] = {}
        if url is not None:
            values["url"] = url
        if auth_header is not None:
            values["auth_header"] = auth_header
        if auth_value is not None:
            values["auth_value_ct"] = await kms.encrypt(
                auth_value.encode("utf-8"), {"user_id": str(user_id)}
            )
        elif clear_auth_value:
            values["auth_value_ct"] = None
        if cache_tools_list is not None:
            values["cache_tools_list"] = cache_tools_list
        if enabled is not None:
            values["enabled"] = enabled
        if not values:
            return await self.get_user(slug)
        stmt = (
            update(McpServer)
            .where(
                and_(
                    McpServer.scope == "user",
                    McpServer.user_id == user_id,
                    McpServer.slug == slug,
                    McpServer.is_current.is_(True),
                )
            )
            .values(**values)
            .returning(McpServer)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def delete_user(self, slug: str) -> bool:
        user_id = UUID(await self.current_user_id())
        stmt = (
            delete(McpServer)
            .where(
                and_(
                    McpServer.scope == "user",
                    McpServer.user_id == user_id,
                    McpServer.slug == slug,
                )
            )
            .returning(McpServer.id)
        )
        result = (await self.session.execute(stmt)).first()
        return result is not None

    # ---------- layered resolution ----------

    async def list_effective(
        self, opt_out_slugs: Iterable[str] = ()
    ) -> list[ResolvedMcpServer]:
        """Return official + user MCP servers, enabled-only, resolved.

        MCP has no bundled tier — there's no MCP server we ship with the
        image. Disabled rows are dropped (the agent factory wouldn't
        connect to them anyway).
        """
        opt_outs = set(opt_out_slugs)
        officials = await self.list_official()
        users = await self.list_user()

        mandatory_official_slugs = {o.slug for o in officials if o.mandatory}

        merged: dict[str, ResolvedMcpServer] = {}
        for o in officials:
            if not o.enabled:
                continue
            if not o.mandatory and o.slug in opt_outs:
                continue
            merged[o.slug] = ResolvedMcpServer(
                slug=o.slug,
                scope="official",
                url=o.url,
                auth_header=o.auth_header,
                auth_value_ct=bytes(o.auth_value_ct) if o.auth_value_ct else None,
                cache_tools_list=o.cache_tools_list,
                enabled=o.enabled,
                mandatory=o.mandatory,
                user_id=None,
            )
        for u in users:
            if not u.enabled:
                continue
            if u.slug in mandatory_official_slugs:
                continue
            merged[u.slug] = ResolvedMcpServer(
                slug=u.slug,
                scope="user",
                url=u.url,
                auth_header=u.auth_header,
                auth_value_ct=bytes(u.auth_value_ct) if u.auth_value_ct else None,
                cache_tools_list=u.cache_tools_list,
                enabled=u.enabled,
                mandatory=False,
                user_id=str(u.user_id) if u.user_id is not None else None,
            )
        return sorted(merged.values(), key=lambda m: m.slug)

    async def record_probe(
        self, slug: str, *, ok: bool, when: datetime
    ) -> None:
        """Update ``last_probe_at``/``last_probe_ok`` after a probe call."""
        user_id = UUID(await self.current_user_id())
        stmt = (
            update(McpServer)
            .where(
                and_(
                    McpServer.scope == "user",
                    McpServer.user_id == user_id,
                    McpServer.slug == slug,
                    McpServer.is_current.is_(True),
                )
            )
            .values(last_probe_at=when, last_probe_ok=ok)
        )
        await self.session.execute(stmt)
