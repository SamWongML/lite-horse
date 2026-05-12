"""Local-FS :class:`FeedbackSink` — NDJSON to ``~/.litehorse/feedback.log``.

CLI parity backend. Every ``record`` call appends one JSON line; the two
read methods scan the file lazily. The log is append-only — no rotation
or compaction beyond what the user does manually, matching v0.4's
``feedback.log`` shape for evolve consumption.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from lite_horse.agent.backends.feedback import (
    FeedbackSink,
    OutcomeRecord,
    OutcomeSource,
    OutcomeStats,
)
from lite_horse.constants import litehorse_home

_FEEDBACK_LOG_NAME = "feedback.log"


def _feedback_path() -> Path:
    return litehorse_home() / _FEEDBACK_LOG_NAME


class FeedbackLocalBackend(FeedbackSink):
    """NDJSON-backed feedback sink rooted at ``~/.litehorse/feedback.log``."""

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or _feedback_path()

    async def record(
        self,
        *,
        session_id: str,
        turn_id: str,
        source: OutcomeSource,
        rating: int,
        reason: str | None = None,
        skill_slug: str | None = None,
    ) -> OutcomeRecord:
        ts_iso = datetime.now(UTC).isoformat()
        record = OutcomeRecord(
            session_id=session_id,
            turn_id=turn_id,
            source=source,
            rating=int(rating),
            reason=reason,
            skill_slug=skill_slug,
            ts_iso=ts_iso,
        )
        line = json.dumps(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "source": source,
                "rating": int(rating),
                "reason": reason,
                "skill_slug": skill_slug,
                "ts": ts_iso,
            },
            separators=(",", ":"),
        )
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return record

    async def latest_for_turn(self, turn_id: str) -> OutcomeRecord | None:
        latest: OutcomeRecord | None = None
        for rec in self._read_records():
            if rec.turn_id == turn_id:
                latest = rec
        return latest

    async def rating_stats(self, *, skill_slug: str) -> OutcomeStats:
        uses = success = errors = 0
        for rec in self._read_records():
            if rec.skill_slug != skill_slug:
                continue
            uses += 1
            if rec.rating == 1:
                success += 1
            elif rec.rating == -1:
                errors += 1
        return OutcomeStats(
            use_count=uses, success_count=success, error_count=errors
        )

    def _read_records(self) -> list[OutcomeRecord]:
        path = self.path
        if not path.exists():
            return []
        out: list[OutcomeRecord] = []
        try:
            with path.open("r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    source = data.get("source")
                    if source not in {
                        "classifier",
                        "user_explicit",
                        "regex_marker",
                    }:
                        continue
                    out.append(
                        OutcomeRecord(
                            session_id=str(data.get("session_id") or ""),
                            turn_id=str(data.get("turn_id") or ""),
                            source=source,
                            rating=int(data.get("rating") or 0),
                            reason=data.get("reason"),
                            skill_slug=data.get("skill_slug"),
                            ts_iso=str(data.get("ts") or ""),
                        )
                    )
        except OSError:
            return []
        return out
