"""agents table — per-user persona + tool bundle + per-agent caps.

Phase 41 introduces the multi-agent axis: each user owns one or more
agents (``coder``, ``shopper``, ``writer``, ...). Existing per-user state
(skills, cron jobs, memory, sessions) attaches to one agent via
``agent_id``; ``users.default_agent_id`` points at the user's "main"
agent (auto-created on first login).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lite_horse.models.base import Base


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint(
            "permission_mode IN ('auto','ask','ro')",
            name="agents_permission_mode_check",
        ),
        UniqueConstraint("user_id", "slug", name="agents_user_slug_unique"),
        Index(
            "agents_one_default_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    persona: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    default_model: Mapped[str | None] = mapped_column(String, nullable=True)
    permission_mode: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'auto'")
    )
    enabled_tools: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    rate_limit_per_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_budget_usd_micro: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
