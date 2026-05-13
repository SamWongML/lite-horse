"""phase 45 — skill promotion candidates + opt-in GEPA flag

Revision ID: 0007_phase45_promotion
Revises: 0006_phase44_curator
Create Date: 2026-06-04

Adds ``skill_promotion_candidates`` so a high-quality user-scope skill
can surface in the admin UI as a candidate for the ``official`` scope.
The table is **admin-only**: no RLS is enabled because the promotion
tick runs without an ``app.user_id`` GUC and the admin endpoints are
gated by JWT ``role=admin`` (not by Postgres tenancy). Source rows
remain RLS-protected by the user-scope ``skills`` policy.

A nullable ``frontmatter_name`` column captures the resolved
``frontmatter.name`` at aggregation time so identical skill names from
different users can be coalesced even when their slug differs.

The same migration also pins the table to a single pending row per
``(frontmatter_name)`` via a partial unique index — the daily tick
upserts on this key.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_phase45_promotion"
down_revision: str | None = "0006_phase44_curator"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_promotion_candidates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "source_skill_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("frontmatter_name", sa.String(), nullable=False),
        sa.Column("unique_user_count", sa.Integer(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("success_rate", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "promoted_skill_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skills.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "generated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "decided_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('pending','promoted','rejected')",
            name="skill_promotion_candidates_status_check",
        ),
    )
    op.create_index(
        "skill_promotion_candidates_status_ts",
        "skill_promotion_candidates",
        ["status", sa.text("generated_at DESC")],
    )
    # One pending row per frontmatter.name so the daily tick can upsert
    # on it. Decided rows (promoted/rejected) stay around as history.
    op.execute(
        "CREATE UNIQUE INDEX skill_promotion_candidates_pending_name "
        "ON skill_promotion_candidates (frontmatter_name) "
        "WHERE status = 'pending'"
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS skill_promotion_candidates_pending_name"
    )
    op.drop_index(
        "skill_promotion_candidates_status_ts",
        table_name="skill_promotion_candidates",
    )
    op.drop_table("skill_promotion_candidates")
