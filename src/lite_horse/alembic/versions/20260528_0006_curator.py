"""curator background pass + outcome classifier

Revision ID: 0006_curator
Revises: 0005_session_summaries
Create Date: 2026-05-28

Adds the durable surface the curator + outcome classifier need:

* ``turn_outcomes`` — one row per classified or user-fed-back turn,
  scoped to ``(user_id, agent_id, session_id, turn_id)`` with a
  ``source ∈ {classifier, user_explicit, regex_marker}`` discriminator
  and a ``rating ∈ {-1, 0, 1}`` signal. RLS + FORCE applies the same
  compound user/agent ``tenant_isolation`` policy as the rest of the
  tables so admin / curator passes that don't set ``app.agent_id``
  still see the user's full slice via the empty-string fallback.
* ``skills`` gains ``use_count`` / ``success_count`` / ``error_count`` /
  ``last_used_at`` / ``curator_state`` columns so the curator's daily
  transition pass (``active → stale → archived``) has somewhere to write.

No data backfill is needed: existing ``skills`` rows default to
``use_count=0``, ``curator_state='active'`` and the curator's first run
will transition them based on age + outcome counters.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_curator"
down_revision: str | None = "0005_session_summaries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "turn_outcomes",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
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
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "turn_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("skill_slug", sa.String(), nullable=True),
        sa.Column(
            "ts",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source IN ('classifier','user_explicit','regex_marker')",
            name="turn_outcomes_source_check",
        ),
        sa.CheckConstraint(
            "rating BETWEEN -1 AND 1", name="turn_outcomes_rating_check"
        ),
    )
    op.create_index(
        "turn_outcomes_agent_ts",
        "turn_outcomes",
        ["user_id", "agent_id", sa.text("ts DESC")],
    )
    op.create_index(
        "turn_outcomes_turn",
        "turn_outcomes",
        ["user_id", "turn_id"],
    )

    op.execute("ALTER TABLE turn_outcomes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE turn_outcomes FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON turn_outcomes "
        "USING (user_id::text = current_setting('app.user_id', true) "
        "AND (current_setting('app.agent_id', true) = '' "
        "OR agent_id::text = current_setting('app.agent_id', true)))"
    )

    # Curator counter columns on skills. All five default to safe values so
    # existing rows don't need a backfill — the curator's first pass treats
    # use_count=0 + last_used_at IS NULL as a never-used skill and applies
    # its own transition rules.
    op.add_column(
        "skills",
        sa.Column(
            "use_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "skills",
        sa.Column(
            "success_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "skills",
        sa.Column(
            "error_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "skills",
        sa.Column(
            "last_used_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "skills",
        sa.Column(
            "curator_state",
            sa.String(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )
    op.create_check_constraint(
        "skills_curator_state_check",
        "skills",
        "curator_state IN ('active','stale','archived','pinned')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "skills_curator_state_check", "skills", type_="check"
    )
    op.drop_column("skills", "curator_state")
    op.drop_column("skills", "last_used_at")
    op.drop_column("skills", "error_count")
    op.drop_column("skills", "success_count")
    op.drop_column("skills", "use_count")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON turn_outcomes")
    op.execute("ALTER TABLE turn_outcomes NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE turn_outcomes DISABLE ROW LEVEL SECURITY")
    op.drop_index("turn_outcomes_turn", table_name="turn_outcomes")
    op.drop_index("turn_outcomes_agent_ts", table_name="turn_outcomes")
    op.drop_table("turn_outcomes")
