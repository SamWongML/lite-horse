"""turn_outcomes table — one row per classified or feedback-rated turn (Phase 44)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lite_horse.models.base import Base


class TurnOutcome(Base):
    __tablename__ = "turn_outcomes"
    __table_args__ = (
        CheckConstraint(
            "source IN ('classifier','user_explicit','regex_marker')",
            name="turn_outcomes_source_check",
        ),
        CheckConstraint(
            "rating BETWEEN -1 AND 1",
            name="turn_outcomes_rating_check",
        ),
        Index(
            "turn_outcomes_agent_ts",
            "user_id",
            "agent_id",
            text("ts DESC"),
        ),
        Index("turn_outcomes_turn", "user_id", "turn_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String, nullable=False)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    skill_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
