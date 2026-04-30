"""phase 39 — per-user rate limit + daily cost budget columns

Revision ID: 0002_phase39_user_limits
Revises: 0001_initial
Create Date: 2026-04-30

Adds two nullable columns on ``users`` so each row can override the
process-default turn rate cap (``rate_limit_per_min``) and set a daily
cost budget enforced in Redis (``cost_budget_usd_micro``). NULL keeps
the existing default behaviour, so the migration is non-disruptive on
upgrade.

Phase 39 also locks RLS as defence-in-depth — RLS was enabled in
``0001_initial`` already; this revision additionally calls ``ALTER
TABLE ... FORCE ROW LEVEL SECURITY`` on the four tenant-scoped tables
so the RLS policy fires even when the connection role owns the table.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_phase39_user_limits"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = ("messages", "sessions", "user_documents", "skill_proposals")


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("rate_limit_per_min", sa.Integer(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("cost_budget_usd_micro", sa.BigInteger(), nullable=True),
    )
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.drop_column("users", "cost_budget_usd_micro")
    op.drop_column("users", "rate_limit_per_min")
