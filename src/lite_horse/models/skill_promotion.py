"""skill_promotion_candidates — admin-only promotion queue."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lite_horse.models.base import Base


class SkillPromotionCandidate(Base):
    __tablename__ = "skill_promotion_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','promoted','rejected')",
            name="skill_promotion_candidates_status_check",
        ),
        Index(
            "skill_promotion_candidates_status_ts",
            "status",
            text("generated_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    frontmatter_name: Mapped[str] = mapped_column(String, nullable=False)
    unique_user_count: Mapped[int] = mapped_column(Integer, nullable=False)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False)
    success_rate: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'pending'")
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    promoted_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True,
    )
    generated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
