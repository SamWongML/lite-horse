"""Single declarative Base for v0.4 ORM models."""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base. All v0.4 ORM tables register against this."""
