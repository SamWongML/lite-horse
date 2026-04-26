"""ORM models register every locked-DDL table and constraint."""
from __future__ import annotations

from sqlalchemy import CheckConstraint, Index

from lite_horse.models import Base

EXPECTED_TABLES = {
    "users",
    "skills",
    "instructions",
    "commands",
    "mcp_servers",
    "cron_jobs",
    "user_official_opt_outs",
    "user_documents",
    "sessions",
    "messages",
    "skill_proposals",
    "audit_log",
    "usage_events",
}


def test_every_locked_ddl_table_is_modelled() -> None:
    assert set(Base.metadata.tables.keys()) == EXPECTED_TABLES


def test_layered_tables_have_partial_unique_indexes() -> None:
    for name in ("skills", "instructions", "commands"):
        idx_names = {idx.name for idx in Base.metadata.tables[name].indexes}
        assert f"{name}_official_slug_current" in idx_names
        assert f"{name}_user_slug_current" in idx_names

    mcp_idx = {idx.name for idx in Base.metadata.tables["mcp_servers"].indexes}
    assert {"mcp_official_slug_current", "mcp_user_slug_current"} <= mcp_idx


def test_layered_tables_have_scope_check() -> None:
    for name in ("skills", "instructions", "commands", "mcp_servers", "cron_jobs"):
        constraints = {
            c.name
            for c in Base.metadata.tables[name].constraints
            if isinstance(c, CheckConstraint)
        }
        assert f"{name}_scope_check" in constraints
        assert f"{name}_scope_user_id_check" in constraints


def test_messages_has_generated_tsvector() -> None:
    msgs = Base.metadata.tables["messages"]
    tsv = msgs.c["tsv"]
    assert tsv.computed is not None
    # GIN index on tsv for FTS speed.
    by_name: dict[str, Index] = {
        str(idx.name): idx for idx in msgs.indexes if idx.name is not None
    }
    assert by_name["messages_tsv"].dialect_options["postgresql"]["using"] == "gin"


def test_users_role_and_permission_mode_constraints() -> None:
    users = Base.metadata.tables["users"]
    constraint_names = {
        c.name for c in users.constraints if isinstance(c, CheckConstraint)
    }
    assert "users_role_check" in constraint_names
    assert "users_permission_mode_check" in constraint_names


def test_opt_out_entity_check() -> None:
    table = Base.metadata.tables["user_official_opt_outs"]
    constraint_names = {
        c.name for c in table.constraints if isinstance(c, CheckConstraint)
    }
    assert "user_official_opt_outs_entity_check" in constraint_names


def test_user_documents_kind_check() -> None:
    table = Base.metadata.tables["user_documents"]
    constraint_names = {
        c.name for c in table.constraints if isinstance(c, CheckConstraint)
    }
    assert "user_documents_kind_check" in constraint_names
