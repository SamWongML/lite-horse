"""memory_chunks table — pgvector-indexed semantic recall (Phase 42).

One row per chunked source fragment from ``memory.md``, ``user.md``,
session summaries, message bodies, or skill bodies. The ``tsv`` column
is populated by Postgres (``GENERATED ALWAYS``) for BM25; ``embedding``
is a 1536-dim ``vector(1536)`` column populated by the embedding worker
or, in CLI mode, inline before insertion.

The model is intentionally minimal — pgvector's ``Vector`` type is held
as ``LargeBinary`` here to avoid pulling the optional ``pgvector`` Python
adapter into the import graph; reads that need cosine distance use raw
SQL through :mod:`lite_horse.repositories.memory_chunk_repo`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from lite_horse.models.base import Base


class _Vector1536(UserDefinedType[Any]):
    """Minimal type stub for pgvector's ``vector(1536)`` column.

    The repo passes/reads the column as a Python list of floats; we
    only need SQLAlchemy to round-trip the column shape for migrations
    + reflection, not to implement the full pgvector wire protocol.
    """

    cache_ok = True

    def get_col_spec(self, **_: Any) -> str:  # pragma: no cover - DDL only
        return "vector(1536)"


class MemoryChunk(Base):
    __tablename__ = "memory_chunks"
    __table_args__ = (
        CheckConstraint(
            "source_kind IN ("
            "'memory_md','user_md','session_summary','message','skill_body'"
            ")",
            name="memory_chunks_source_kind_check",
        ),
        Index(
            "memory_chunks_tenant",
            "user_id",
            "agent_id",
            text("ts DESC"),
        ),
        Index("memory_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "memory_chunks_source",
            "user_id",
            "agent_id",
            "source_kind",
            "source_id",
        ),
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
    source_kind: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    chunk_index: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', content)", persisted=True),
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        _Vector1536(), nullable=True
    )
    embed_model: Mapped[str] = mapped_column(String, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
