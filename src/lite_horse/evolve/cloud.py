"""Cloud-shaped evolve enqueue + dispatch — Phase 39.

The v0.3 ``evolve.runner`` walks ``~/.litehorse/skills/`` and writes
proposals to disk. The cloud port keeps the same gate logic but its
inputs come from Postgres (trajectory counts on ``messages``) and its
output sinks into ``skill_proposals`` instead of a file. The worker
service consumes :class:`EvolveMessage` instances off SQS; the scheduler
enqueues one per (user x skill) that has crossed the trajectory
threshold since last evolve.

This module owns the **queue contract** (``EvolveMessage``), the
**eligibility rule** (``find_evolve_candidates``), and the
**worker entry-point** (``run_evolve``). The actual reflection +
constraints + fitness scoring stays in :mod:`lite_horse.evolve.runner`
and is invoked behind ``run_evolve`` once the message reaches the
worker.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from lite_horse.models.skill_proposal import SkillProposal
from lite_horse.storage.db import db_session

log = logging.getLogger(__name__)

#: Minimum new trajectories per (user, skill) before evolve runs.
EVOLVE_THRESHOLD_TRAJECTORIES = 10
#: Cooldown after a successful evolve run, in days.
EVOLVE_COOLDOWN_DAYS = 7
#: Marker on the queue body so the worker dispatches to the right path.
EVOLVE_KIND = "evolve"


@dataclass(frozen=True)
class EvolveMessage:
    """One queued evolve run: rerun reflection over a user's skill."""

    kind: str
    user_id: str
    skill_slug: str
    enqueued_at: str

    @classmethod
    def new(cls, *, user_id: str, skill_slug: str, now: datetime | None = None) -> EvolveMessage:
        moment = now or datetime.now(UTC)
        return cls(
            kind=EVOLVE_KIND,
            user_id=user_id,
            skill_slug=skill_slug,
            enqueued_at=moment.isoformat(),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> EvolveMessage:
        data = json.loads(raw)
        return cls(
            kind=str(data["kind"]),
            user_id=str(data["user_id"]),
            skill_slug=str(data["skill_slug"]),
            enqueued_at=str(data["enqueued_at"]),
        )


def is_evolve_payload(raw: str) -> bool:
    """Return True if the queue body looks like an :class:`EvolveMessage`.

    Cheap structural check — the worker uses it before parsing so cron
    fan-out messages stay on the cron path.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("kind") == EVOLVE_KIND


@dataclass(frozen=True)
class EvolveCandidate:
    user_id: str
    skill_slug: str
    trajectory_count: int


async def find_evolve_candidates(
    *,
    threshold: int = EVOLVE_THRESHOLD_TRAJECTORIES,
    cooldown_days: int = EVOLVE_COOLDOWN_DAYS,
    now: datetime | None = None,
) -> list[EvolveCandidate]:
    """Scan recent activity for (user, skill) pairs eligible for evolve.

    Returns an empty list until the message store carries a
    ``skill_slug`` discriminator (introduced in a follow-up to Phase
    39). The framework — queue contract, worker dispatch, scheduler
    wiring — is fully in place; only the eligibility query is deferred.
    """
    del threshold, cooldown_days, now
    return []


async def run_evolve(message: EvolveMessage) -> bool:
    """Execute one evolve run from the worker side.

    Inserts a stub :class:`SkillProposal` row tagged ``status='pending'``
    so the admin / user surface can see the run was attempted; full
    reflection + constraint gating is delegated to
    :func:`lite_horse.evolve.runner.evolve` in a follow-up wire-up that
    swaps the FS-backed read for the PG-backed trajectory miner. The
    cloud entrypoint here is the queue contract; gate logic stays in
    one place.
    """
    user_uuid = uuid.UUID(message.user_id)
    proposal_id = uuid.uuid4()
    fitness: dict[str, Any] = {
        "trigger": "scheduled",
        "enqueued_at": message.enqueued_at,
    }
    async with db_session(user_id=message.user_id) as session:
        session.add(
            SkillProposal(
                id=proposal_id,
                user_id=user_uuid,
                skill_slug=message.skill_slug,
                base_version=None,
                body="# pending — evolve worker has not produced a draft yet\n",
                fitness=fitness,
                status="pending",
            )
        )
    log.info(
        "evolve queued proposal slug=%s user=%s id=%s",
        message.skill_slug,
        message.user_id,
        proposal_id,
    )
    return True
