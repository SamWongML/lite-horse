"""audit_log writer for admin actions.

Every admin write is wrapped via :class:`AuditRepo.log` so the
``audit_log`` table records actor, action, target, and a before/after
diff. Reads land under ``GET /v1/admin/audit-log``.

Audit rows are append-only and not RLS-gated — admins can list across
the whole org. The query layer is intentionally narrow (filter by
``actor_id`` / ``action`` / ``since``) to keep the surface small.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select

from lite_horse.models.audit_log import AuditLog
from lite_horse.repositories.base import BaseRepo


class AuditRepo(BaseRepo):
    """audit_log CRUD; admins are the only legitimate readers."""

    async def log(
        self,
        *,
        actor_id: str,
        actor_role: str,
        action: str,
        target: dict[str, Any],
        diff: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> AuditLog:
        row = AuditLog(
            actor_id=uuid.UUID(actor_id),
            actor_role=actor_role,
            action=action,
            target=target,
            diff=diff,
            request_id=uuid.UUID(request_id) if request_id else None,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list(
        self,
        *,
        actor_id: str | None = None,
        action: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)
        if actor_id is not None:
            stmt = stmt.where(AuditLog.actor_id == uuid.UUID(actor_id))
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        if since is not None:
            stmt = stmt.where(AuditLog.ts >= since)
        return list((await self.session.execute(stmt)).scalars().all())
