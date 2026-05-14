"""GDPR delete requests + hardening

Revision ID: 0008_hardening
Revises: 0007_skill_promotion
Create Date: 2026-06-12

Adds ``gdpr_delete_requests`` — one queued row per user-initiated
"delete my account" request. The HTTP endpoint writes a row with
``scheduled_at = now() + 7 days`` so the user gets a grace window to
cancel; a daily worker tick scans for due rows, exports the user's
data to S3, purges every tenant-scoped table, anonymises the matching
``audit_log.actor_id`` to a tombstone value, and stamps
``completed_at`` + ``archive_s3_key``.

RLS is intentionally **off** because the worker runs without an
``app.user_id`` GUC (and indeed needs to read across users when the
grace window fires). The table is reached only through the GDPR
worker and the user-scope HTTP routes; the routes resolve
``user_id`` from the request context, never from the body.

A partial unique index on ``(user_id) WHERE completed_at IS NULL``
keeps the request idempotent — a user with a pending request
gets a 409 on a second POST until they cancel or the worker fires.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_hardening"
down_revision: str | None = "0007_skill_promotion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gdpr_delete_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "requested_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "scheduled_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "cancelled_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("archive_s3_key", sa.String(), nullable=True),
    )
    op.execute(
        "CREATE UNIQUE INDEX gdpr_delete_requests_pending_user "
        "ON gdpr_delete_requests (user_id) "
        "WHERE completed_at IS NULL AND cancelled_at IS NULL"
    )
    op.create_index(
        "gdpr_delete_requests_due",
        "gdpr_delete_requests",
        ["scheduled_at"],
        postgresql_where=sa.text(
            "completed_at IS NULL AND cancelled_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "gdpr_delete_requests_due", table_name="gdpr_delete_requests"
    )
    op.execute("DROP INDEX IF EXISTS gdpr_delete_requests_pending_user")
    op.drop_table("gdpr_delete_requests")
