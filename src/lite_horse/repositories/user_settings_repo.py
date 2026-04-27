"""Per-user settings — ``users.default_model`` and ``users.permission_mode``.

The ``users`` row is created lazily in ``web.auth`` on first JWT sight; this
repo exposes the safe-to-edit settings columns. ``byo_provider_key_*`` is
intentionally NOT in scope here — KMS-encrypted BYO keys land in Phase 37
with their own write path.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update

from lite_horse.models.user import User
from lite_horse.repositories.base import BaseRepo

VALID_PERMISSION_MODES = ("auto", "ask", "ro")


@dataclass(frozen=True)
class UserSettings:
    default_model: str | None
    permission_mode: str


class UserSettingsRepo(BaseRepo):
    """users.default_model + users.permission_mode."""

    async def get(self) -> UserSettings:
        user_id = UUID(await self.current_user_id())
        stmt = select(User.default_model, User.permission_mode).where(
            User.id == user_id
        )
        row = (await self.session.execute(stmt)).one()
        return UserSettings(default_model=row[0], permission_mode=row[1])

    async def update(
        self,
        *,
        default_model: str | None = None,
        clear_default_model: bool = False,
        permission_mode: str | None = None,
    ) -> UserSettings:
        if permission_mode is not None and permission_mode not in VALID_PERMISSION_MODES:
            raise ValueError(f"invalid permission_mode: {permission_mode!r}")
        values: dict[str, object] = {}
        if default_model is not None:
            values["default_model"] = default_model
        elif clear_default_model:
            values["default_model"] = None
        if permission_mode is not None:
            values["permission_mode"] = permission_mode
        if values:
            user_id = UUID(await self.current_user_id())
            await self.session.execute(
                update(User).where(User.id == user_id).values(**values)
            )
        return await self.get()
