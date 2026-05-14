"""gdpr_delete_requests — queued user-initiated GDPR delete.

One row per outstanding "delete my account" request. The HTTP route
writes ``requested_at = now()`` and ``scheduled_at = now() + 7 days``;
:func:`run_gdpr_delete` flips ``completed_at`` once the purge succeeds
or ``cancelled_at`` if the user cancels first. The partial unique
index ``gdpr_delete_requests_pending_user`` keeps one open request per
user — replays return 409 until the row is resolved.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lite_horse.models.base import Base


class GdprDeleteRequest(Base):
    __tablename__ = "gdpr_delete_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    archive_s3_key: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
