"""multi-agent: agents table + agent_id on tenant-scoped rows

Revision ID: 0003_agents
Revises: 0002_user_limits
Create Date: 2026-05-07

Introduces the per-user multi-agent axis. After this migration:

* ``agents`` table holds one or more agents per user, with persona,
  default_model, permission_mode, enabled_tools, per-agent rate / cost
  caps, ``is_default``, and ``archived_at`` for soft-delete.
* Every existing user gets one auto-created default agent
  (``slug='default'``, ``name='default'``, ``is_default=true``); existing
  user-scope rows in ``user_documents``, ``skills``, ``cron_jobs``,
  ``sessions``, ``skill_proposals``, ``mcp_servers``, ``commands``,
  ``instructions`` are backfilled to that default agent.
* ``users.default_agent_id`` points at the auto-created agent.
* RLS policies on ``user_documents``, ``sessions``, ``skill_proposals``
  are extended to the compound form
  ``user_id::text = current_setting('app.user_id', true)
  AND (current_setting('app.agent_id', true) = ''
  OR agent_id::text = current_setting('app.agent_id', true))``.
  ``messages`` keeps its original user-only RLS (no ``agent_id``
  column on that table).

The migration is non-disruptive: callers that don't yet set the
``app.agent_id`` GUC (admin tasks) keep seeing all rows for the user
via the empty-string fallback.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_agents"
down_revision: str | None = "0002_user_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_AGENT_ID_TABLES = (
    "user_documents",
    "skills",
    "cron_jobs",
    "sessions",
    "skill_proposals",
    "mcp_servers",
    "commands",
    "instructions",
)

_USER_ONLY_AGENT_ID_TABLES = (
    "user_documents",
    "sessions",
    "skill_proposals",
)

_LAYERED_AGENT_ID_TABLES = (
    "skills",
    "cron_jobs",
    "mcp_servers",
    "commands",
    "instructions",
)

_RLS_AGENT_TABLES = ("user_documents", "sessions", "skill_proposals")


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "persona", sa.Text(), nullable=False, server_default=sa.text("''")
        ),
        sa.Column("default_model", sa.String(), nullable=True),
        sa.Column(
            "permission_mode",
            sa.String(),
            nullable=False,
            server_default=sa.text("'auto'"),
        ),
        sa.Column(
            "enabled_tools",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("rate_limit_per_min", sa.Integer(), nullable=True),
        sa.Column("cost_budget_usd_micro", sa.BigInteger(), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "archived_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "permission_mode IN ('auto','ask','ro')",
            name="agents_permission_mode_check",
        ),
        sa.UniqueConstraint("user_id", "slug", name="agents_user_slug_unique"),
    )
    op.create_index(
        "agents_one_default_per_user",
        "agents",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    # users.default_agent_id (nullable FK; populated after backfill).
    op.add_column(
        "users",
        sa.Column(
            "default_agent_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_users_default_agent",
        "users",
        "agents",
        ["default_agent_id"],
        ["id"],
        use_alter=True,
    )

    # Add nullable agent_id columns on every affected table.
    for table in _AGENT_ID_TABLES:
        op.add_column(
            table,
            sa.Column(
                "agent_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
        )
        op.create_foreign_key(
            f"fk_{table}_agent",
            table,
            "agents",
            ["agent_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # Backfill: one default agent per existing user.
    op.execute(
        """
        INSERT INTO agents (
            id, user_id, slug, name, persona, permission_mode,
            enabled_tools, is_default, created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            u.id,
            'default',
            'default',
            '',
            'auto',
            '[]'::jsonb,
            true,
            now(),
            now()
        FROM users u
        WHERE NOT EXISTS (
            SELECT 1 FROM agents a WHERE a.user_id = u.id AND a.is_default
        )
        """
    )

    # Point users.default_agent_id at each user's default agent.
    op.execute(
        """
        UPDATE users u
        SET default_agent_id = a.id
        FROM agents a
        WHERE a.user_id = u.id AND a.is_default
        """
    )

    # Backfill agent_id on every user-scope row.
    op.execute(
        """
        UPDATE user_documents d
        SET agent_id = a.id
        FROM agents a
        WHERE a.user_id = d.user_id AND a.is_default
        """
    )
    op.execute(
        """
        UPDATE sessions s
        SET agent_id = a.id
        FROM agents a
        WHERE a.user_id = s.user_id AND a.is_default
        """
    )
    op.execute(
        """
        UPDATE skill_proposals sp
        SET agent_id = a.id
        FROM agents a
        WHERE a.user_id = sp.user_id AND a.is_default
        """
    )
    for table in _LAYERED_AGENT_ID_TABLES:
        op.execute(
            f"""
            UPDATE {table} t
            SET agent_id = a.id
            FROM agents a
            WHERE t.scope = 'user'
              AND t.user_id = a.user_id
              AND a.is_default
            """
        )

    # Tighten constraints — user-only tables get NOT NULL on agent_id;
    # layered tables get a CHECK that matches the existing scope/user_id
    # discipline so official rows leave agent_id NULL and user rows must
    # have one.
    for table in _USER_ONLY_AGENT_ID_TABLES:
        op.alter_column(table, "agent_id", nullable=False)

    for table in _LAYERED_AGENT_ID_TABLES:
        op.create_check_constraint(
            f"{table}_scope_agent_id_check",
            table,
            "(scope='user' AND agent_id IS NOT NULL) "
            "OR (scope='official' AND agent_id IS NULL)",
        )

    # RLS — extend tenant_isolation on the three tables that gained
    # agent_id with the compound (user_id AND agent_id) policy. The
    # empty-string fallback preserves admin / curator queries that
    # don't set app.agent_id.
    for table in _RLS_AGENT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (user_id::text = current_setting('app.user_id', true) "
            "AND (current_setting('app.agent_id', true) = '' "
            "OR agent_id::text = current_setting('app.agent_id', true)))"
        )


def downgrade() -> None:
    # Restore original user-only RLS policies on the three tables.
    for table in _RLS_AGENT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (user_id::text = current_setting('app.user_id', true))"
        )

    for table in _LAYERED_AGENT_ID_TABLES:
        op.drop_constraint(
            f"{table}_scope_agent_id_check", table, type_="check"
        )

    op.drop_constraint(
        "fk_users_default_agent", "users", type_="foreignkey"
    )
    op.drop_column("users", "default_agent_id")

    for table in _AGENT_ID_TABLES:
        op.drop_constraint(f"fk_{table}_agent", table, type_="foreignkey")
        op.drop_column(table, "agent_id")

    op.drop_index("agents_one_default_per_user", table_name="agents")
    op.drop_table("agents")
