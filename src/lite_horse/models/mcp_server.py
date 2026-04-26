"""mcp_servers table — layered MCP registry with KMS-encrypted auth values."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lite_horse.models.base import Base


class McpServer(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (
        CheckConstraint("scope IN ('official','user')", name="mcp_servers_scope_check"),
        CheckConstraint(
            "(scope='user' AND user_id IS NOT NULL) "
            "OR (scope='official' AND user_id IS NULL)",
            name="mcp_servers_scope_user_id_check",
        ),
        Index(
            "mcp_official_slug_current",
            "slug",
            unique=True,
            postgresql_where=text("scope='official' AND is_current"),
        ),
        Index(
            "mcp_user_slug_current",
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
    url: Mapped[str] = mapped_column(String, nullable=False)
    auth_header: Mapped[str | None] = mapped_column(String, nullable=True)
    auth_value_ct: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    auth_value_dk: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    cache_tools_list: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    mandatory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    last_probe_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_probe_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
