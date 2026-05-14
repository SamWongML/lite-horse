"""pgvector recall: memory_chunks + ``vector`` extension

Revision ID: 0004_pgvector
Revises: 0003_agents
Create Date: 2026-05-14

Lifts the 2 400-char wholesale-injection ceiling. After this migration:

* ``vector`` extension is enabled on the database (``CREATE EXTENSION IF
  NOT EXISTS vector``); the migration is a no-op if pgvector is already
  installed.
* ``memory_chunks`` table holds one row per chunked source fragment with
  ``content`` (text), ``tsv`` (generated tsvector for BM25), and
  ``embedding`` (1536-dim vector). HNSW index on cosine + GIN index on
  ``tsv`` + a tenant index on ``(user_id, agent_id, ts DESC)``.
* RLS extended on ``memory_chunks`` with the compound user/agent policy
  the rest of v0.5 uses; admin connections without ``app.agent_id`` see
  every row for the user via the empty-string fallback.

The HNSW build memory is bounded for the duration of the index creation
via ``SET maintenance_work_mem = '512MB'`` so a small RDS instance
doesn't OOM. The setting is ``LOCAL`` to the migration transaction.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_pgvector"
down_revision: str | None = "0003_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("SET LOCAL maintenance_work_mem = '512MB'")

    op.create_table(
        "memory_chunks",
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
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embed_model", sa.String(), nullable=False),
        sa.Column(
            "ts",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source_kind IN ("
            "'memory_md','user_md','session_summary','message','skill_body'"
            ")",
            name="memory_chunks_source_kind_check",
        ),
    )

    # ``tsv`` is a generated column on ``content``. SQLAlchemy 2 doesn't
    # expose pgvector or generated tsvector directly via ``Column`` so we
    # add both as raw DDL after the table exists.
    op.execute(
        "ALTER TABLE memory_chunks "
        "ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED"
    )
    op.execute("ALTER TABLE memory_chunks ADD COLUMN embedding vector(1536)")

    op.create_index(
        "memory_chunks_tenant",
        "memory_chunks",
        ["user_id", "agent_id", sa.text("ts DESC")],
    )
    op.execute(
        "CREATE INDEX memory_chunks_tsv ON memory_chunks USING GIN (tsv)"
    )
    op.execute(
        "CREATE INDEX memory_chunks_vec ON memory_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.create_index(
        "memory_chunks_source",
        "memory_chunks",
        ["user_id", "agent_id", "source_kind", "source_id"],
    )

    op.execute("ALTER TABLE memory_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memory_chunks FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON memory_chunks "
        "USING (user_id::text = current_setting('app.user_id', true) "
        "AND (current_setting('app.agent_id', true) = '' "
        "OR agent_id::text = current_setting('app.agent_id', true)))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON memory_chunks")
    op.execute("ALTER TABLE memory_chunks NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memory_chunks DISABLE ROW LEVEL SECURITY")
    op.drop_index("memory_chunks_source", table_name="memory_chunks")
    op.execute("DROP INDEX IF EXISTS memory_chunks_vec")
    op.execute("DROP INDEX IF EXISTS memory_chunks_tsv")
    op.drop_index("memory_chunks_tenant", table_name="memory_chunks")
    op.drop_table("memory_chunks")
    # The ``vector`` extension is left enabled — other migrations or
    # databases may rely on it. Dropping it on downgrade would be a
    # data-loss surprise.
