"""``CronBackend`` Protocol — tenant-safe cron job CRUD.

Local impl writes to ``~/.litehorse/jobs.json`` via :class:`JobStore`; cloud
impl writes to ``cron_jobs`` via :class:`CronRepo`. The tool wire shape is
frozen (see ``cron_manage``); the backend translates that shape into the
per-impl storage call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CronJobView:
    """Tool-facing cron job snapshot.

    ``id`` is the local 12-hex job id under the local backend and the slug
    under the cloud backend; either way it's the address agents pass back
    in for remove / enable / disable.
    """

    id: str
    schedule: str
    prompt: str
    delivery: dict[str, Any]
    enabled: bool
    disabled_reason: str | None = None


@runtime_checkable
class CronBackend(Protocol):
    async def list_jobs(self) -> list[CronJobView]:
        """List all cron jobs visible to the current tenant."""
        ...

    async def add(
        self, *, schedule: str, prompt: str, delivery: dict[str, Any]
    ) -> CronJobView:
        """Insert a new cron job. Caller has already validated schedule + delivery."""
        ...

    async def remove(self, job_id: str) -> bool:
        """Delete by id (local) or slug (cloud). Returns False when missing."""
        ...

    async def set_enabled(self, job_id: str, *, enabled: bool) -> bool:
        """Flip enabled flag. Returns False when missing."""
        ...
