"""user_official_opt_outs table — per-user filter for non-mandatory official entities."""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from lite_horse.models.base import Base


class UserOfficialOptOut(Base):
    __tablename__ = "user_official_opt_outs"
    __table_args__ = (
        CheckConstraint(
            "entity IN ('skill','instruction','command','mcp_server','cron_job')",
            name="user_official_opt_outs_entity_check",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity: Mapped[str] = mapped_column(String, primary_key=True)
    slug: Mapped[str] = mapped_column(String, primary_key=True)
