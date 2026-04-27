"""Cross-tenant user listing — admin / scheduler use only.

The per-tenant ``UserSettingsRepo`` reads the GUC and stays inside one
user's row. The scheduler service runs **without** an ``app.user_id``
GUC (admin-bypass path) and needs to enumerate users to fan an
official-scope cron job out, so it gets its own narrow repository.

"Active" today means ``role='user'`` and the row exists. Phase 39
hardening tightens this with a last-seen filter.
"""
from __future__ import annotations

from sqlalchemy import select

from lite_horse.models.user import User
from lite_horse.repositories.base import BaseRepo


class UserRepo(BaseRepo):
    async def list_active_user_ids(self) -> list[str]:
        stmt = select(User.id).where(User.role == "user").order_by(User.created_at)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [str(uid) for uid in rows]
