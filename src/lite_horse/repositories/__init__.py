"""Tenant-scoped Postgres repositories.

Per the v0.4 Hard Contract this is the **only** package allowed to issue
raw SQL against a request-scoped `AsyncSession`. Every method honours the
`app.user_id` GUC set by `storage.db.db_session` either by RLS (defence
in depth) or explicit `WHERE user_id = ...` filters (the offence layer
RLS backs up).

`SearchHit` is re-exported here so callers can grab the FTS row shape
without reaching across packages.
"""

from lite_horse.repositories.base import BaseRepo, audited
from lite_horse.repositories.message_repo import MessageRepo
from lite_horse.repositories.session_repo import SessionRepo
from lite_horse.sessions.types import SearchHit

__all__ = ["BaseRepo", "MessageRepo", "SearchHit", "SessionRepo", "audited"]
