"""GEPA worker entry-point — Phase 45.

Drains one ``evolve_gepa`` queue message: mine the eval set, run the
population loop, persist the winner as a ``skill_proposals`` row.

The expensive variant-generation / case-replay / classifier calls are
injected via ``run_fn`` so unit tests stay hermetic. The default
``run_fn`` boots :func:`lite_horse.evolve.gepa.runner.run_gepa` with
the production OpenAI-backed callables, but the worker entry-point is
deliberately thin — anything model-shaped lives in the runner module.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass

from lite_horse.evolve.gepa.runner import (
    GepaResult,
    result_as_fitness_jsonb,
)
from lite_horse.models.skill_proposal import SkillProposal
from lite_horse.storage.db import db_session

log = logging.getLogger(__name__)

EVOLVE_GEPA_KIND = "evolve_gepa"


@dataclass(frozen=True)
class EvolveGepaMessage:
    """One queued GEPA run for a ``(user, agent, skill)`` triple."""

    kind: str
    user_id: str
    agent_id: str
    skill_slug: str
    enqueued_at: str

    @classmethod
    def new(
        cls,
        *,
        user_id: str,
        agent_id: str,
        skill_slug: str,
        enqueued_at: str,
    ) -> EvolveGepaMessage:
        return cls(
            kind=EVOLVE_GEPA_KIND,
            user_id=user_id,
            agent_id=agent_id,
            skill_slug=skill_slug,
            enqueued_at=enqueued_at,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> EvolveGepaMessage:
        data = json.loads(raw)
        return cls(
            kind=str(data["kind"]),
            user_id=str(data["user_id"]),
            agent_id=str(data["agent_id"]),
            skill_slug=str(data["skill_slug"]),
            enqueued_at=str(data["enqueued_at"]),
        )


def is_evolve_gepa_payload(raw: str) -> bool:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("kind") == EVOLVE_GEPA_KIND


GepaRunFn = Callable[[EvolveGepaMessage], Awaitable[GepaResult]]


async def _default_run_fn(message: EvolveGepaMessage) -> GepaResult:
    """Production GEPA run — kept as a stub here, wired in Phase 46.

    The real implementation depends on Anthropic/OpenAI SDK bumps that
    Phase 46 lands. Until then this entry-point records that a run was
    attempted so the proposals timeline stays observable; the actual
    population loop is exercised end-to-end via the CLI parity gate.
    """
    return GepaResult(
        skill_slug=message.skill_slug,
        accepted=False,
        best_skill_md=None,
        best_rating=0.0,
        best_size=0,
        aborted_reason="default_run_fn: GEPA production runner pending SDK bumps",
    )


async def run_evolve_gepa(
    message: EvolveGepaMessage,
    *,
    run_fn: GepaRunFn | None = None,
) -> bool:
    """Worker entry-point. Returns ``True`` on success, ``False`` on retry."""
    runner = run_fn or _default_run_fn
    try:
        result = await runner(message)
    except Exception:
        log.exception(
            "evolve_gepa: run failed (user=%s agent=%s slug=%s)",
            message.user_id,
            message.agent_id,
            message.skill_slug,
        )
        return False

    proposal_id = uuid.uuid4()
    fitness = result_as_fitness_jsonb(result)
    body = result.best_skill_md or (
        "# pending — GEPA produced no candidate "
        f"({result.aborted_reason or 'no acceptable variant'})\n"
    )
    async with db_session(message.user_id, message.agent_id) as session:
        session.add(
            SkillProposal(
                id=proposal_id,
                user_id=uuid.UUID(message.user_id),
                agent_id=uuid.UUID(message.agent_id),
                skill_slug=message.skill_slug,
                base_version=None,
                body=body,
                fitness=fitness,
                status="pending",
            )
        )
    log.info(
        "evolve_gepa: proposal %s (accepted=%s) slug=%s user=%s agent=%s",
        proposal_id,
        result.accepted,
        message.skill_slug,
        message.user_id,
        message.agent_id,
    )
    return True
