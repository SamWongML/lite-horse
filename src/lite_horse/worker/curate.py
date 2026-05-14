"""Curate worker.

Runs the :class:`Curator` daily over one ``(user_id, agent_id)`` slice
of ``skills``. State transitions live in :mod:`lite_horse.curator`
so the worker is a thin queue+dispatch shell:

    {"kind": "curate", "user_id": "...", "agent_id": "..."}

The curator's auxiliary side-agent (consolidation proposals) is the
costly part — it runs only when two user-scope skills are within the
:data:`CURATOR_CONSOLIDATE_COSINE` threshold; otherwise the worker
only touches counters + ``curator_state``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from lite_horse.curator import Curator, CuratorReport

log = logging.getLogger(__name__)

CURATE_KIND = "curate"
DEFAULT_CURATOR_MODEL = "gpt-5.4-mini"


@dataclass(frozen=True)
class CurateMessage:
    """One queued curator pass for one ``(user, agent)`` slice."""

    kind: str
    user_id: str
    agent_id: str
    model: str = ""

    @classmethod
    def new(
        cls,
        *,
        user_id: str,
        agent_id: str,
        model: str = "",
    ) -> CurateMessage:
        return cls(
            kind=CURATE_KIND,
            user_id=user_id,
            agent_id=agent_id,
            model=model,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> CurateMessage:
        data = json.loads(raw)
        return cls(
            kind=str(data["kind"]),
            user_id=str(data["user_id"]),
            agent_id=str(data["agent_id"]),
            model=str(data.get("model") or ""),
        )


def is_curate_payload(raw: str) -> bool:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("kind") == CURATE_KIND


async def run_curate(
    message: CurateMessage,
    *,
    curator: Curator | None = None,
) -> bool:
    """Run one curator pass. Returns ``True`` on success, ``False`` on retry."""
    model = message.model or DEFAULT_CURATOR_MODEL
    cur = curator or Curator(model=model)
    try:
        report: CuratorReport = await cur.run_for_agent(
            user_id=message.user_id, agent_id=message.agent_id
        )
    except Exception:
        log.exception(
            "curate: pass failed (user=%s agent=%s)",
            message.user_id,
            message.agent_id,
        )
        return False
    log.info(
        "curate: user=%s agent=%s active=%d stale=%d archived=%d "
        "proposals=%d",
        message.user_id,
        message.agent_id,
        report.kept_active,
        report.transitioned_stale,
        report.transitioned_archived,
        report.consolidation_proposals,
    )
    return True
