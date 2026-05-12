"""phase 43 — per-session summaries that survive into the next session

Revision ID: 0005_phase43_summaries
Revises: 0004_phase42_pgvector
Create Date: 2026-05-21

A side-agent summarises every completed session into a 1–3 sentence blurb
that the prompt assembly path injects into the next session, plus the
recall pgvector store from Phase 42 (``source_kind='session_summary'``).
This migration just creates the durable table; the worker + summarizer
side-agent + injection live in code.

The compound user/agent ``tenant_isolation`` policy + FORCE RLS match
the rest of the v0.5 tables so admin / curator passes that don't set
``app.agent_id`` still see the user's full slice via the empty-string
fallback.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phase43_summaries"
down_revision: str | None = "0004_phase42_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_summaries",
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(), nullable=True),
        sa.Column(
            "generated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("generator", sa.String(), nullable=False),
    )
    op.create_index(
        "session_summaries_tenant",
        "session_summaries",
        ["user_id", "agent_id", sa.text("generated_at DESC")],
    )

    op.execute("ALTER TABLE session_summaries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE session_summaries FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON session_summaries "
        "USING (user_id::text = current_setting('app.user_id', true) "
        "AND (current_setting('app.agent_id', true) = '' "
        "OR agent_id::text = current_setting('app.agent_id', true)))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON session_summaries")
    op.execute("ALTER TABLE session_summaries NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE session_summaries DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "session_summaries_tenant", table_name="session_summaries"
    )
    op.drop_table("session_summaries")
