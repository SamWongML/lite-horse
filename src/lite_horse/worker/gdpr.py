"""GDPR delete worker.

Daily tick + dispatcher route ``kind="gdpr_delete"`` messages here. Each
message names one due :class:`GdprDeleteRequest`; the handler:

1. Resolves the request row (skip if cancelled / completed since enqueue).
2. Exports every tenant-scoped table for the user as a single JSON
   document under ``LITEHORSE_S3_BUCKET_AUDIT_ARCHIVE`` at
   ``gdpr/<user_id>/<request_id>.json`` (versioning + Glacier lifecycle
   live in the bucket; no per-key TTL).
3. In one transaction, deletes from every tenant-scoped table
   (cascades cover most; we sweep the rows the cascade can't reach),
   then anonymises ``audit_log.actor_id`` to a tombstone value so the
   audit trail stays intact but the identity is scrubbed.
4. Stamps ``gdpr_delete_requests.completed_at`` + ``archive_s3_key``.

The wire shape is intentionally narrow — the message body just carries
``request_id``; the handler reads everything else from Postgres so the
queue can't drift from the schedule.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import delete, update

from lite_horse.models.audit_log import AuditLog
from lite_horse.models.cron_job import CronJob
from lite_horse.models.mcp_server import McpServer
from lite_horse.models.user import User
from lite_horse.repositories.gdpr_delete_repo import GdprDeleteRepo
from lite_horse.storage import make_blob_store
from lite_horse.storage.blob import BlobStore
from lite_horse.storage.db import db_session

log = logging.getLogger(__name__)

GDPR_DELETE_KIND = "gdpr_delete"

# Tombstone UUID — replaces ``audit_log.actor_id`` so the audit history
# stays intact while the identity is scrubbed. Picked deterministically
# so reports can group "deleted user" rows.
GDPR_TOMBSTONE_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-00000deadbee")

@dataclass(frozen=True)
class GdprDeleteMessage:
    kind: str
    request_id: str

    @classmethod
    def new(cls, *, request_id: str) -> GdprDeleteMessage:
        return cls(kind=GDPR_DELETE_KIND, request_id=request_id)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> GdprDeleteMessage:
        data = json.loads(raw)
        return cls(
            kind=str(data["kind"]),
            request_id=str(data["request_id"]),
        )


def is_gdpr_delete_payload(raw: str) -> bool:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("kind") == GDPR_DELETE_KIND


async def _collect_export(user_id: str) -> dict[str, list[dict[str, object]]]:
    """Dump every tenant-scoped row for ``user_id`` to a JSON-able dict.

    Runs inside one tenant-scoped transaction so RLS narrows reads to
    the target user. The raw-SQL ``SELECT *`` lives on
    :meth:`GdprDeleteRepo.dump_tenant_rows` so the worker stays free of
    raw SQL (enforced by the no-raw-sql-outside-repos lint rule).
    """
    async with db_session(user_id) as session:
        return await GdprDeleteRepo(session).dump_tenant_rows()


async def _purge_tenant_rows(user_id: str) -> None:
    """Delete every tenant-scoped row and tombstone audit_log rows.

    Runs in admin scope (no ``app.user_id`` GUC) so the worker can reach
    rows that wouldn't be visible under the user's RLS policy after a
    partial failure. The order matches the foreign-key topology — leaf
    rows first, ``users`` last.
    """
    user_uuid = uuid.UUID(user_id)
    async with db_session(user_id=None) as session:
        # Anonymise audit log first — admins still want the history rows.
        await session.execute(
            update(AuditLog)
            .where(AuditLog.actor_id == user_uuid)
            .values(actor_id=GDPR_TOMBSTONE_ACTOR_ID)
        )
        # Cron + MCP user-scope rows: cascade fires from users but we sweep
        # explicitly so the operation is order-stable.
        await session.execute(
            delete(CronJob).where(CronJob.user_id == user_uuid)
        )
        await session.execute(
            delete(McpServer).where(McpServer.user_id == user_uuid)
        )
        # Final ``DELETE FROM users`` cascades the remaining tenant tables.
        await session.execute(
            delete(User).where(User.id == user_uuid)
        )


async def run_gdpr_delete(  # noqa: PLR0911 — branch-per-failure mode is the readable shape
    message: GdprDeleteMessage,
    *,
    blob_store: BlobStore | None = None,
) -> bool:
    """Run one GDPR delete end-to-end. Returns ``True`` on success."""
    request_id = uuid.UUID(message.request_id)
    store = blob_store or make_blob_store("audit")

    async with db_session(user_id=None) as session:
        row = await GdprDeleteRepo(session).get_by_id(request_id)
    if row is None:
        log.warning("gdpr_delete: request %s not found", message.request_id)
        return True
    if row.completed_at is not None or row.cancelled_at is not None:
        log.info(
            "gdpr_delete: request %s already settled — skipping",
            message.request_id,
        )
        return True
    if row.user_id is None:
        log.warning(
            "gdpr_delete: request %s has null user_id — already purged",
            message.request_id,
        )
        return True
    user_id = str(row.user_id)

    try:
        export = await _collect_export(user_id)
    except Exception:
        log.exception(
            "gdpr_delete: export failed (user=%s request=%s)",
            user_id,
            message.request_id,
        )
        return False
    archive_key = f"gdpr/{user_id}/{message.request_id}.json"
    payload = json.dumps(
        {"user_id": user_id, "request_id": message.request_id, "data": export},
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        await store.put(archive_key, payload, "application/json")
    except Exception:
        log.exception(
            "gdpr_delete: archive upload failed (key=%s)", archive_key
        )
        return False

    try:
        await _purge_tenant_rows(user_id)
    except Exception:
        log.exception(
            "gdpr_delete: purge failed (user=%s request=%s)",
            user_id,
            message.request_id,
        )
        return False

    # The FK on ``gdpr_delete_requests.user_id`` is ``ON DELETE SET NULL``
    # so the row survives the user purge; ``mark_completed`` stamps
    # ``completed_at`` + ``archive_s3_key`` for the audit trail.
    async with db_session(user_id=None) as session:
        repo = GdprDeleteRepo(session)
        completed = await repo.mark_completed(
            request_id, archive_s3_key=archive_key
        )
    log.info(
        "gdpr_delete: completed user=%s request=%s archive=%s settled=%s",
        user_id,
        message.request_id,
        archive_key,
        completed is not None,
    )
    return True


__all__ = [
    "GDPR_DELETE_KIND",
    "GDPR_TOMBSTONE_ACTOR_ID",
    "GdprDeleteMessage",
    "is_gdpr_delete_payload",
    "run_gdpr_delete",
]
