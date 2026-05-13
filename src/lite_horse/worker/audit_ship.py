"""Audit-archive shipper — Phase 46.

A worker tick uploads ``audit_log`` rows older than 90 days into the
audit-archive bucket as JSONL, then deletes them from Postgres. The
bucket already has versioning + Glacier lifecycle from v0.4, so the
files transition to cold storage on their own.

The plan calls for Parquet, but a Parquet writer drags pyarrow into the
worker image for what is fundamentally a low-volume archival shipper.
JSONL stays inside the v0.4 dep set and is trivially recoverable; if
analytics needs columnar reads later, a separate compaction job can
convert the JSONL once it's in S3.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from lite_horse.models.audit_log import AuditLog
from lite_horse.storage import make_blob_store
from lite_horse.storage.blob import BlobStore
from lite_horse.storage.db import db_session

log = logging.getLogger(__name__)

AUDIT_SHIP_KIND = "audit_ship"
DEFAULT_RETENTION_DAYS = 90
DEFAULT_BATCH_LIMIT = 1000


@dataclass(frozen=True)
class AuditShipMessage:
    kind: str
    retention_days: int = DEFAULT_RETENTION_DAYS
    limit: int = DEFAULT_BATCH_LIMIT

    @classmethod
    def new(
        cls,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        limit: int = DEFAULT_BATCH_LIMIT,
    ) -> AuditShipMessage:
        return cls(
            kind=AUDIT_SHIP_KIND,
            retention_days=retention_days,
            limit=limit,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> AuditShipMessage:
        data = json.loads(raw)
        return cls(
            kind=str(data["kind"]),
            retention_days=int(data.get("retention_days", DEFAULT_RETENTION_DAYS)),
            limit=int(data.get("limit", DEFAULT_BATCH_LIMIT)),
        )


def is_audit_ship_payload(raw: str) -> bool:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("kind") == AUDIT_SHIP_KIND


def _row_to_jsonable(row: AuditLog) -> dict[str, object]:
    return {
        "id": int(row.id),
        "ts": row.ts.isoformat() if row.ts else None,
        "actor_id": str(row.actor_id),
        "actor_role": row.actor_role,
        "action": row.action,
        "target": row.target,
        "diff": row.diff,
        "request_id": str(row.request_id) if row.request_id else None,
    }


async def run_audit_ship(
    message: AuditShipMessage,
    *,
    blob_store: BlobStore | None = None,
    now: datetime | None = None,
) -> bool:
    """Ship one batch of expired audit rows to S3. Returns ``True`` on success.

    Idempotent: a re-run of the same key range produces a new
    timestamp-suffixed object key (versioning on the bucket keeps both).
    A failure leaves the rows in Postgres for the next tick.
    """
    store = blob_store or make_blob_store("audit")
    cutoff = (now or datetime.now(UTC)) - timedelta(
        days=int(message.retention_days)
    )

    async with db_session(user_id=None) as session:
        stmt = (
            select(AuditLog)
            .where(AuditLog.ts < cutoff)
            .order_by(AuditLog.id.asc())
            .limit(int(message.limit))
        )
        rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            log.info("audit_ship: no rows older than %s", cutoff.isoformat())
            return True
        payload = "\n".join(
            json.dumps(_row_to_jsonable(r), separators=(",", ":"))
            for r in rows
        ).encode("utf-8")

    key = (
        f"audit/{cutoff.strftime('%Y%m%d')}/"
        f"{uuid.uuid4()}-{rows[0].id}-{rows[-1].id}.jsonl"
    )
    try:
        await store.put(key, payload, "application/x-ndjson")
    except Exception:
        log.exception("audit_ship: upload failed (key=%s)", key)
        return False

    ids = [int(r.id) for r in rows]
    async with db_session(user_id=None) as session:
        await session.execute(delete(AuditLog).where(AuditLog.id.in_(ids)))

    log.info(
        "audit_ship: shipped %d rows to %s (cutoff=%s)",
        len(rows),
        key,
        cutoff.isoformat(),
    )
    return True


__all__ = [
    "AUDIT_SHIP_KIND",
    "AuditShipMessage",
    "is_audit_ship_payload",
    "run_audit_ship",
]
