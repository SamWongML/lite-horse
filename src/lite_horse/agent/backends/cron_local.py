"""Local-FS :class:`CronBackend` impl — wraps :class:`JobStore`.

State lives at ``~/.litehorse/jobs.json``.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from lite_horse.agent.backends.cron import CronBackend, CronJobView
from lite_horse.cron.jobs import JobStore


def _to_view(d: dict[str, Any]) -> CronJobView:
    return CronJobView(
        id=str(d["id"]),
        schedule=d["schedule"],
        prompt=d["prompt"],
        delivery=dict(d["delivery"]),
        enabled=bool(d.get("enabled", True)),
        disabled_reason=d.get("disabled_reason"),
    )


class CronLocalBackend(CronBackend):
    """JSON-file-backed cron jobs at ``~/.litehorse/jobs.json``."""

    def __init__(self, store: JobStore | None = None) -> None:
        self._store = store or JobStore()

    async def list_jobs(self) -> list[CronJobView]:
        return [_to_view(asdict(j)) for j in self._store.all()]

    async def add(
        self, *, schedule: str, prompt: str, delivery: dict[str, Any]
    ) -> CronJobView:
        job = self._store.add(schedule=schedule, prompt=prompt, delivery=delivery)
        return _to_view(asdict(job))

    async def remove(self, job_id: str) -> bool:
        return self._store.remove(job_id)

    async def set_enabled(self, job_id: str, *, enabled: bool) -> bool:
        return self._store.set_enabled(job_id, enabled)
