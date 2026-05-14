"""``gdpr_delete_requests`` repository.

One row per user-initiated delete. Two surfaces:

* User-scope (HTTP): the route opens a tenant-scoped ``db_session``
  with the caller's ``user_id``; :meth:`upsert_pending` creates a
  pending row (409 if one already exists), :meth:`cancel_pending`
  flips ``cancelled_at`` if the worker hasn't fired yet.

* Admin (worker): the daily tick opens a tenantless ``db_session``
  and calls :meth:`list_due` to find rows whose ``scheduled_at`` has
  passed; :meth:`mark_completed` stamps the archive key when the
  purge succeeds.

The table is intentionally **not** RLS-gated — the worker needs to
scan across users, and the HTTP routes already constrain ``user_id``
to the authenticated caller's id via :meth:`current_user_id`.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text, update

from lite_horse.models.gdpr_delete_request import GdprDeleteRequest
from lite_horse.repositories.base import BaseRepo

# Tenant-scoped tables the GDPR worker dumps to S3 before purging. Order
# is the read order; under RLS each ``SELECT *`` is narrowed to the
# authenticated user, so an admin context here would return everything
# (which is why the worker calls :meth:`dump_tenant_rows` from a
# tenant-scoped session, not an admin one).
GDPR_TENANT_TABLES: tuple[str, ...] = (
    "memory_chunks",
    "turn_outcomes",
    "session_summaries",
    "messages",
    "sessions",
    "user_documents",
    "skill_proposals",
    "agents",
    "usage_events",
)


def _stringify(v: object) -> object:
    """Coerce values JSON serialisers can't handle natively."""
    if isinstance(v, _uuid.UUID):
        return str(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return v


class GdprDeleteRepo(BaseRepo):
    """``gdpr_delete_requests`` CRUD."""

    async def get_by_id(self, request_id: UUID) -> GdprDeleteRequest | None:
        stmt = select(GdprDeleteRequest).where(
            GdprDeleteRequest.id == request_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def dump_tenant_rows(self) -> dict[str, list[dict[str, object]]]:
        """Dump every row visible under the current tenant scope.

        Must be called from a tenant-scoped ``db_session(user_id=...)``
        so RLS narrows the reads to the authenticated user. The fixed
        :data:`GDPR_TENANT_TABLES` list is the v0.5 plan's set of
        per-user tables; admin-scoped tables (``audit_log``, ``users``)
        do not appear here and are handled separately by the worker.
        """
        export: dict[str, list[dict[str, object]]] = {}
        for table in GDPR_TENANT_TABLES:
            rows = (
                await self.session.execute(text(f"SELECT * FROM {table}"))
            ).mappings().all()
            export[table] = [
                {k: _stringify(v) for k, v in row.items()} for row in rows
            ]
        return export

    async def get_pending_for_user(
        self, user_id: str
    ) -> GdprDeleteRequest | None:
        stmt = (
            select(GdprDeleteRequest)
            .where(GdprDeleteRequest.user_id == UUID(user_id))
            .where(GdprDeleteRequest.completed_at.is_(None))
            .where(GdprDeleteRequest.cancelled_at.is_(None))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: str,
        scheduled_at: datetime,
    ) -> GdprDeleteRequest:
        row = GdprDeleteRequest(
            id=uuid4(),
            user_id=UUID(user_id),
            scheduled_at=scheduled_at,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def cancel_pending(self, user_id: str) -> GdprDeleteRequest | None:
        stmt = (
            update(GdprDeleteRequest)
            .where(GdprDeleteRequest.user_id == UUID(user_id))
            .where(GdprDeleteRequest.completed_at.is_(None))
            .where(GdprDeleteRequest.cancelled_at.is_(None))
            .values(cancelled_at=datetime.now(UTC))
            .returning(GdprDeleteRequest)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_due(
        self, *, now: datetime, limit: int = 100
    ) -> list[GdprDeleteRequest]:
        stmt = (
            select(GdprDeleteRequest)
            .where(GdprDeleteRequest.completed_at.is_(None))
            .where(GdprDeleteRequest.cancelled_at.is_(None))
            .where(GdprDeleteRequest.scheduled_at <= now)
            .order_by(GdprDeleteRequest.scheduled_at.asc())
            .limit(int(limit))
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def mark_completed(
        self, request_id: UUID, *, archive_s3_key: str
    ) -> GdprDeleteRequest | None:
        stmt = (
            update(GdprDeleteRequest)
            .where(GdprDeleteRequest.id == request_id)
            .where(GdprDeleteRequest.completed_at.is_(None))
            .values(
                completed_at=datetime.now(UTC),
                archive_s3_key=archive_s3_key,
            )
            .returning(GdprDeleteRequest)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


__all__ = ["GdprDeleteRepo"]
