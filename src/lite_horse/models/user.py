"""users table — identity + per-user defaults + KMS-encrypted BYO keys."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Integer, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lite_horse.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user','admin')", name="users_role_check"),
        CheckConstraint(
            "permission_mode IN ('auto','ask','ro')",
            name="users_permission_mode_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    external_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'user'")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    default_model: Mapped[str | None] = mapped_column(String, nullable=True)
    permission_mode: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'auto'")
    )
    # KMS envelope-encrypted: ciphertext (ct) + encrypted data key (dk).
    # Holds a JSON document {openai, anthropic, github:{access_token,…}}.
    byo_provider_key_ct: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    byo_provider_key_dk: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    # Phase 39: per-user rate limit + daily cost budget overrides.
    # NULL → use process default; non-positive → unlimited tier.
    rate_limit_per_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_budget_usd_micro: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
