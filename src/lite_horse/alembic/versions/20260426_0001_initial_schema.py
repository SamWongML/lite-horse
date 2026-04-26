"""initial schema — v0.4 multi-tenant DDL

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-26

Creates every table in the locked v0.4 DDL plus partial unique indexes,
the generated tsvector column on `messages`, and RLS policies on the
four tenant-scoped tables (`messages`, `sessions`, `user_documents`,
`skill_proposals`).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.String(), nullable=False, unique=True),
        sa.Column(
            "role", sa.String(), nullable=False, server_default=sa.text("'user'")
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("default_model", sa.String(), nullable=True),
        sa.Column(
            "permission_mode",
            sa.String(),
            nullable=False,
            server_default=sa.text("'auto'"),
        ),
        sa.Column("byo_provider_key_ct", sa.LargeBinary(), nullable=True),
        sa.Column("byo_provider_key_dk", sa.LargeBinary(), nullable=True),
        sa.CheckConstraint("role IN ('user','admin')", name="users_role_check"),
        sa.CheckConstraint(
            "permission_mode IN ('auto','ask','ro')",
            name="users_permission_mode_check",
        ),
    )

    _layered_columns: list[sa.Column[Any]] = [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "mandatory",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    ]

    op.create_table(
        "skills",
        *_clone_columns(_layered_columns),
        sa.Column(
            "enabled_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("frontmatter", postgresql.JSONB(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.CheckConstraint("scope IN ('official','user')", name="skills_scope_check"),
        sa.CheckConstraint(
            "(scope='user' AND user_id IS NOT NULL) "
            "OR (scope='official' AND user_id IS NULL)",
            name="skills_scope_user_id_check",
        ),
    )
    op.create_index(
        "skills_official_slug_current",
        "skills",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("scope='official' AND is_current"),
    )
    op.create_index(
        "skills_user_slug_current",
        "skills",
        ["user_id", "slug"],
        unique=True,
        postgresql_where=sa.text("scope='user' AND is_current"),
    )

    op.create_table(
        "instructions",
        *_clone_columns(_layered_columns),
        sa.Column(
            "priority", sa.Integer(), nullable=False, server_default=sa.text("100")
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "scope IN ('official','user')", name="instructions_scope_check"
        ),
        sa.CheckConstraint(
            "(scope='user' AND user_id IS NOT NULL) "
            "OR (scope='official' AND user_id IS NULL)",
            name="instructions_scope_user_id_check",
        ),
    )
    op.create_index(
        "instructions_official_slug_current",
        "instructions",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("scope='official' AND is_current"),
    )
    op.create_index(
        "instructions_user_slug_current",
        "instructions",
        ["user_id", "slug"],
        unique=True,
        postgresql_where=sa.text("scope='user' AND is_current"),
    )

    op.create_table(
        "commands",
        *_clone_columns(_layered_columns),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("prompt_tpl", sa.Text(), nullable=False),
        sa.Column("arg_schema", postgresql.JSONB(), nullable=True),
        sa.Column("bind_skills", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "scope IN ('official','user')", name="commands_scope_check"
        ),
        sa.CheckConstraint(
            "(scope='user' AND user_id IS NOT NULL) "
            "OR (scope='official' AND user_id IS NULL)",
            name="commands_scope_user_id_check",
        ),
    )
    op.create_index(
        "commands_official_slug_current",
        "commands",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("scope='official' AND is_current"),
    )
    op.create_index(
        "commands_user_slug_current",
        "commands",
        ["user_id", "slug"],
        unique=True,
        postgresql_where=sa.text("scope='user' AND is_current"),
    )

    op.create_table(
        "mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("auth_header", sa.String(), nullable=True),
        sa.Column("auth_value_ct", sa.LargeBinary(), nullable=True),
        sa.Column("auth_value_dk", sa.LargeBinary(), nullable=True),
        sa.Column(
            "cache_tools_list",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "mandatory",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "last_probe_at", postgresql.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("last_probe_ok", sa.Boolean(), nullable=True),
        sa.CheckConstraint(
            "scope IN ('official','user')", name="mcp_servers_scope_check"
        ),
        sa.CheckConstraint(
            "(scope='user' AND user_id IS NOT NULL) "
            "OR (scope='official' AND user_id IS NULL)",
            name="mcp_servers_scope_user_id_check",
        ),
    )
    op.create_index(
        "mcp_official_slug_current",
        "mcp_servers",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("scope='official' AND is_current"),
    )
    op.create_index(
        "mcp_user_slug_current",
        "mcp_servers",
        ["user_id", "slug"],
        unique=True,
        postgresql_where=sa.text("scope='user' AND is_current"),
    )

    op.create_table(
        "cron_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("cron_expr", sa.String(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("webhook_url", sa.String(), nullable=True),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "mandatory",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "last_fired_at", postgresql.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("strikes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint(
            "scope IN ('official','user')", name="cron_jobs_scope_check"
        ),
        sa.CheckConstraint(
            "(scope='user' AND user_id IS NOT NULL) "
            "OR (scope='official' AND user_id IS NULL)",
            name="cron_jobs_scope_user_id_check",
        ),
    )

    op.create_table(
        "user_official_opt_outs",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("entity", sa.String(), primary_key=True),
        sa.Column("slug", sa.String(), primary_key=True),
        sa.CheckConstraint(
            "entity IN ('skill','instruction','command','mcp_server','cron_job')",
            name="user_official_opt_outs_entity_check",
        ),
    )

    op.create_table(
        "user_documents",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('memory.md','user.md')", name="user_documents_kind_check"
        ),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ended_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(), nullable=True),
        sa.Column(
            "message_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "tool_call_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "input_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "output_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("title", sa.Text(), nullable=True),
    )
    op.create_index(
        "sessions_user_started", "sessions", ["user_id", sa.text("started_at DESC")]
    )

    op.create_table(
        "messages",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=True), primary_key=True
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_call_id", sa.String(), nullable=True),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("tool_name", sa.String(), nullable=True),
        sa.Column(
            "ts",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple', coalesce(content, ''))",
                persisted=True,
            ),
        ),
    )
    op.create_index(
        "messages_user_session_ts",
        "messages",
        ["user_id", "session_id", "ts", "id"],
    )
    op.create_index("messages_tsv", "messages", ["tsv"], postgresql_using="gin")

    op.create_table(
        "skill_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("skill_slug", sa.String(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("fitness", postgresql.JSONB(), nullable=True),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("s3_artefact_key", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("decided_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','superseded')",
            name="skill_proposals_status_check",
        ),
    )

    op.create_table(
        "audit_log",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=True), primary_key=True
        ),
        sa.Column(
            "ts",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("actor_role", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", postgresql.JSONB(), nullable=False),
        sa.Column("diff", postgresql.JSONB(), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "audit_log_actor_ts", "audit_log", ["actor_id", sa.text("ts DESC")]
    )
    op.create_index(
        "audit_log_action_ts", "audit_log", ["action", sa.text("ts DESC")]
    )

    op.create_table(
        "usage_events",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=True), primary_key=True
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column(
            "ts",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "cached_input_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("cost_usd_micro", sa.BigInteger(), nullable=False),
    )
    op.create_index("usage_user_ts", "usage_events", ["user_id", sa.text("ts DESC")])

    # Row-Level Security — defence-in-depth tenant isolation. The
    # request-scoped `app.user_id` GUC is set by storage/db.db_session()
    # before any query runs.
    for table in ("messages", "sessions", "user_documents", "skill_proposals"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (user_id::text = current_setting('app.user_id', true))"
        )


def downgrade() -> None:
    for table in ("messages", "sessions", "user_documents", "skill_proposals"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("usage_user_ts", table_name="usage_events")
    op.drop_table("usage_events")

    op.drop_index("audit_log_action_ts", table_name="audit_log")
    op.drop_index("audit_log_actor_ts", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_table("skill_proposals")

    op.drop_index("messages_tsv", table_name="messages")
    op.drop_index("messages_user_session_ts", table_name="messages")
    op.drop_table("messages")

    op.drop_index("sessions_user_started", table_name="sessions")
    op.drop_table("sessions")

    op.drop_table("user_documents")
    op.drop_table("user_official_opt_outs")
    op.drop_table("cron_jobs")

    op.drop_index("mcp_user_slug_current", table_name="mcp_servers")
    op.drop_index("mcp_official_slug_current", table_name="mcp_servers")
    op.drop_table("mcp_servers")

    for table in ("commands", "instructions", "skills"):
        op.drop_index(f"{table}_user_slug_current", table_name=table)
        op.drop_index(f"{table}_official_slug_current", table_name=table)
        op.drop_table(table)

    op.drop_table("users")


def _clone_columns(cols: list[sa.Column[Any]]) -> list[sa.Column[Any]]:
    """A Column instance binds to one Table at most — clone for reuse."""
    return [c.copy() for c in cols]
