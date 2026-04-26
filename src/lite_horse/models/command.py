"""commands table — layered slash-command templates with bound skills."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lite_horse.models.base import Base


class Command(Base):
    __tablename__ = "commands"
    __table_args__ = (
        CheckConstraint("scope IN ('official','user')", name="commands_scope_check"),
        CheckConstraint(
            "(scope='user' AND user_id IS NOT NULL) "
            "OR (scope='official' AND user_id IS NULL)",
            name="commands_scope_user_id_check",
        ),
        Index(
            "commands_official_slug_current",
            "slug",
            unique=True,
            postgresql_where=text("scope='official' AND is_current"),
        ),
        Index(
            "commands_user_slug_current",
            "user_id",
            "slug",
            unique=True,
            postgresql_where=text("scope='user' AND is_current"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    slug: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    mandatory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tpl: Mapped[str] = mapped_column(Text, nullable=False)
    arg_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    bind_skills: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
