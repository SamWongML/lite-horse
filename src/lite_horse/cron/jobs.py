"""JSON-backed cron job store at ``~/.litehorse/jobs.json``.

One process mutates this file at a time (only the cron scheduler writes; the
agent reads via tools), so a simple tmp-file + ``os.replace`` gives us
atomicity without needing a lock.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lite_horse.constants import litehorse_home


@dataclass
class Job:
    """One scheduled job.

    ``schedule`` is either a 5-field crontab (``"min hr day mon dow"``) or one
    of the aliases ``@minutely`` / ``@hourly`` / ``@daily`` / ``@weekly``.
    ``delivery`` is a dict such as ``{"platform": "log"}`` or
    ``{"platform": "webhook", "url": "..."}``.

    ``disabled_reason`` is set by the scheduler when it auto-disables a job —
    currently the only path is three consecutive ``MODEL_REFUSAL`` firings
    (Phase 22). Empty on jobs the user disabled manually.
    """

    id: str
    schedule: str
    prompt: str
    delivery: dict[str, Any]
    enabled: bool = True
    disabled_reason: str | None = None


@dataclass
class JobStore:
    """Flat JSON file holding the job list."""

    path: Path = field(default_factory=lambda: litehorse_home() / "jobs.json")

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def all(self) -> list[Job]:
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        return [Job(**j) for j in raw]

    def get(self, job_id: str) -> Job | None:
        for j in self.all():
            if j.id == job_id:
                return j
        return None

    def add(
        self,
        *,
        schedule: str,
        prompt: str,
        delivery: dict[str, Any],
        enabled: bool = True,
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            schedule=schedule,
            prompt=prompt,
            delivery=delivery,
            enabled=enabled,
        )
        self._write([*self.all(), job])
        return job

    def remove(self, job_id: str) -> bool:
        before = self.all()
        after = [j for j in before if j.id != job_id]
        if len(after) == len(before):
            return False
        self._write(after)
        return True

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        """Flip ``enabled`` on one job. Returns ``False`` if no such job.

        Re-enabling clears ``disabled_reason`` so operator intent wipes the
        auto-disable stamp.
        """
        jobs = self.all()
        for j in jobs:
            if j.id == job_id:
                changed = j.enabled != enabled or (enabled and j.disabled_reason)
                j.enabled = enabled
                if enabled:
                    j.disabled_reason = None
                if changed:
                    self._write(jobs)
                return True
        return False

    def disable_with_reason(self, job_id: str, reason: str) -> bool:
        """Mark a job disabled and stamp ``reason``. Returns False if missing."""
        jobs = self.all()
        for j in jobs:
            if j.id == job_id:
                j.enabled = False
                j.disabled_reason = reason
                self._write(jobs)
                return True
        return False

    def _write(self, jobs: list[Job]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps([asdict(j) for j in jobs], indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)
