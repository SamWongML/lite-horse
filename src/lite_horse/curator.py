"""Curator background pass.

The curator runs once a day per ``(user_id, agent_id)`` slice and is the
slow-loop counterpart to the per-turn :class:`EvolutionHook`. Its
contract is narrow:

* read every user-scope skill for the tenant (current + counters),
* transition state by idle age + outcomes:
  ``last_used_at < now() - 90 days`` AND ``success_count = 0`` →
  ``archived``; ``last_used_at < now() - 30 days`` → ``stale``,
* skip rows whose ``curator_state == 'pinned'`` (operator override),
* when two user-scope skills overlap above
  :data:`CURATOR_CONSOLIDATE_COSINE` in body-embedding cosine,
  spawn a small *curator-reviewer* side-agent to propose a merged
  body and insert one :class:`SkillProposal` row,
* never auto-merge — all changes go through proposals so the user
  approves explicitly via the existing skill-proposal surface.

The class is intentionally a thin orchestrator: state transitions are
plain SQL updates, the side-agent only runs when there's a candidate
pair to merge, and the report is the four counters that
:func:`lite_horse.worker.curate.run_curate` logs after each pass.
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from agents import Agent, Runner

from lite_horse.constants import (
    CURATOR_ARCHIVE_AFTER_DAYS,
    CURATOR_CONSOLIDATE_COSINE,
    CURATOR_STALE_AFTER_DAYS,
)
from lite_horse.models.skill import Skill
from lite_horse.models.skill_proposal import SkillProposal
from lite_horse.providers.embedding import (
    EmbeddingProvider,
    NullEmbeddingProvider,
    select_embedding_provider,
)
from lite_horse.repositories.skill_repo import SkillRepo
from lite_horse.storage.db import db_session

log = logging.getLogger(__name__)

CURATOR_MAX_TURNS = 3
CURATOR_MAX_BODY_CHARS = 1500
CURATOR_REVIEWER_MAX_PAIRS = 5

_REVIEWER_INSTRUCTIONS = (
    "You review two skills that the curator flagged as overlapping. "
    "Propose ONE merged body that preserves every distinct instruction "
    "and removes redundant prose.\n\n"
    "Return STRICT JSON, no prose, no code fences:\n"
    '  {"merge": <true | false>, '
    '"slug": "<dest slug>", '
    '"body": "<merged markdown>", '
    '"reason": "<= 240 chars>"}\n\n'
    "Rules:\n"
    "- merge=false when the overlap is superficial (same topic, but each "
    "skill carries unique instructions); the reason explains why.\n"
    "- merge=true requires the merged body to keep every concrete "
    "instruction from both skills; nothing may silently drop.\n"
    "- slug is the chosen survivor — pick the more general one. The "
    "non-survivor stays in place until the user approves the merge.\n"
    "- body must be markdown with no front-matter and <= 1500 chars."
)


@dataclass(frozen=True)
class CuratorReport:
    """Per-pass counters. Logged by the worker and surfaced to tests."""

    kept_active: int
    transitioned_stale: int
    transitioned_archived: int
    consolidation_proposals: int


@dataclass(frozen=True)
class _Pair:
    a: Skill
    b: Skill
    similarity: float


class Curator:
    """One slow-loop curator pass for one ``(user, agent)`` slice."""

    def __init__(
        self,
        *,
        model: str,
        embedder: EmbeddingProvider | None = None,
        consolidate_threshold: float = CURATOR_CONSOLIDATE_COSINE,
        max_turns: int = CURATOR_MAX_TURNS,
        max_pairs: int = CURATOR_REVIEWER_MAX_PAIRS,
        now_fn: Any = None,
    ) -> None:
        self.model = model
        self._embedder = embedder or select_embedding_provider()
        self.consolidate_threshold = consolidate_threshold
        self.max_turns = max_turns
        self.max_pairs = max_pairs
        self._now = now_fn or (lambda: datetime.now(UTC))

    async def run_for_agent(
        self, *, user_id: str, agent_id: str
    ) -> CuratorReport:
        """Run one pass: transitions + (optional) consolidation proposals."""
        async with db_session(user_id, agent_id) as session:
            repo = SkillRepo(session)
            skills = await repo.list_user()
            now = self._now()
            kept = 0
            staled = 0
            archived = 0
            for s in skills:
                target = self._target_state(s, now=now)
                if target == s.curator_state:
                    if target == "active":
                        kept += 1
                    continue
                await repo.update_curator_state(s.slug, target)
                if target == "stale":
                    staled += 1
                elif target == "archived":
                    archived += 1

            pairs = await self._find_consolidation_pairs(
                [s for s in skills if s.curator_state != "archived"]
            )
            proposals = 0
            for pair in pairs[: self.max_pairs]:
                ok = await self._propose_merge(
                    session=session,
                    user_id=user_id,
                    agent_id=agent_id,
                    pair=pair,
                )
                if ok:
                    proposals += 1

        return CuratorReport(
            kept_active=kept,
            transitioned_stale=staled,
            transitioned_archived=archived,
            consolidation_proposals=proposals,
        )

    # ---------- state transitions ----------

    def _target_state(self, skill: Skill, *, now: datetime) -> str:
        if skill.curator_state == "pinned":
            return "pinned"
        last = skill.last_used_at
        if last is None:
            # Treat never-used rows as aged from creation time.
            last = skill.created_at
        if last is None:
            return skill.curator_state or "active"
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        idle = now - last
        if (
            idle >= timedelta(days=CURATOR_ARCHIVE_AFTER_DAYS)
            and (skill.success_count or 0) == 0
        ):
            return "archived"
        if idle >= timedelta(days=CURATOR_STALE_AFTER_DAYS):
            return "stale"
        return "active"

    # ---------- consolidation ----------

    async def _find_consolidation_pairs(
        self, skills: list[Skill]
    ) -> list[_Pair]:
        if len(skills) < 2 or isinstance(self._embedder, NullEmbeddingProvider):
            return []
        bodies = [(s.body or "")[:CURATOR_MAX_BODY_CHARS] for s in skills]
        try:
            embeddings = await self._embedder.embed_batch(bodies)
        except Exception:
            log.warning("curator: embed_batch failed; skipping consolidation")
            return []
        pairs: list[_Pair] = []
        for i, a in enumerate(skills):
            va = embeddings[i] if i < len(embeddings) else []
            if not va:
                continue
            for j in range(i + 1, len(skills)):
                vb = embeddings[j] if j < len(embeddings) else []
                if not vb:
                    continue
                sim = _cosine(va, vb)
                if sim >= self.consolidate_threshold:
                    pairs.append(_Pair(a=a, b=skills[j], similarity=sim))
        pairs.sort(key=lambda p: p.similarity, reverse=True)
        return pairs

    async def _propose_merge(
        self,
        *,
        session: Any,
        user_id: str,
        agent_id: str,
        pair: _Pair,
    ) -> bool:
        agent = Agent(
            name="curator-reviewer",
            model=self.model,
            instructions=_REVIEWER_INSTRUCTIONS,
        )
        prompt = json.dumps(
            {
                "similarity": round(pair.similarity, 4),
                "a": {
                    "slug": pair.a.slug,
                    "body": (pair.a.body or "")[:CURATOR_MAX_BODY_CHARS],
                },
                "b": {
                    "slug": pair.b.slug,
                    "body": (pair.b.body or "")[:CURATOR_MAX_BODY_CHARS],
                },
            },
            default=str,
        )
        try:
            result = await Runner.run(agent, prompt, max_turns=self.max_turns)
        except Exception:
            log.exception(
                "curator: reviewer side-agent failed (slugs=%s,%s)",
                pair.a.slug,
                pair.b.slug,
            )
            return False
        output = getattr(result, "final_output", None)
        decision = _parse_reviewer(str(output) if output else "")
        if decision is None or not decision.get("merge"):
            return False
        slug = str(decision.get("slug") or pair.a.slug)
        body = str(decision.get("body") or "").strip()
        if not body:
            return False
        reason = str(decision.get("reason") or "")[:240]
        proposal = SkillProposal(
            id=uuid.uuid4(),
            user_id=uuid.UUID(user_id),
            agent_id=uuid.UUID(agent_id),
            skill_slug=slug,
            base_version=None,
            body=body[:CURATOR_MAX_BODY_CHARS],
            fitness={
                "trigger": "curator",
                "similarity": round(pair.similarity, 4),
                "source_slugs": [pair.a.slug, pair.b.slug],
                "reason": reason,
            },
            status="pending",
        )
        session.add(proposal)
        log.info(
            "curator: proposal queued (user=%s agent=%s slugs=%s,%s -> %s)",
            user_id,
            agent_id,
            pair.a.slug,
            pair.b.slug,
            slug,
        )
        return True


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    return float(dot / denom) if denom else 0.0


def _parse_reviewer(output: str) -> dict[str, Any] | None:
    stripped = output.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").lstrip("json").strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        log.warning("curator-reviewer: failed to parse JSON response")
        return None
    if not isinstance(data, dict):
        return None
    return data
