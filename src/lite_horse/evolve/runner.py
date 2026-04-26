"""End-to-end evolve loop for a single skill.

``evolve(skill_name)`` mines recent failures, asks the reflector for a revised
SKILL.md, runs it through the constraint gates, and — if every gate passes —
writes the candidate to ``~/.litehorse/skills/.proposals/<skill>/<ts>.md``
plus a JSON sidecar containing per-gate reasons and fitness scores. The loop
never mutates the skill itself; approval is a separate webapp action.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

import yaml

from lite_horse.evolve import constraints, fitness, reflector, trace_miner
from lite_horse.sessions.local import LocalSessionRepo
from lite_horse.skills.source import skills_root

_PROPOSALS_DIRNAME = ".proposals"


@dataclass
class EvolveResult:
    skill_name: str
    approved: bool
    gates: dict[str, dict[str, Any]]
    fitness: dict[str, float] | None
    proposal_path: str | None
    sidecar_path: str | None
    reason: str = ""


def evolve(
    skill_name: str,
    *,
    db: LocalSessionRepo | None = None,
    reflector_fn: reflector.Reflector | None = None,
    judge: fitness.Judge | None = None,
    embedder: fitness.Embedder | None = None,
    pytest_runner: constraints.PytestRunner | None = None,
    days: int = 14,
    now: float | None = None,
) -> EvolveResult:
    root = skills_root()
    skill_dir = root / skill_name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return _failed(skill_name, "skill_missing", f"no SKILL.md at {skill_md}")

    baseline = skill_md.read_text(encoding="utf-8")
    baseline_fm = _parse_frontmatter(baseline)
    baseline_name = baseline_fm.get("name") or skill_name
    baseline_version = baseline_fm.get("version") if isinstance(
        baseline_fm.get("version"), int
    ) else None

    db = db or LocalSessionRepo()
    trajectories = trace_miner.mine_failures(skill_name, db=db, days=days)

    reflect = reflector_fn or reflector.default_reflector
    candidate = reflect(baseline, trajectories)

    gates = _run_gates(
        candidate=candidate,
        baseline=baseline,
        baseline_name=baseline_name,
        baseline_version=baseline_version,
        pytest_runner=pytest_runner,
    )

    if not all(g.passed for g in gates.values() if g.name != "cosine"):
        return _rejected(skill_name, gates, fitness_score=None)

    judge_fn = judge or fitness.default_judge
    embed_fn = embedder or fitness.default_embedder
    score = fitness.score(
        candidate=candidate,
        baseline=baseline,
        trajectories=trajectories,
        judge=judge_fn,
        embedder=embed_fn,
    )
    gates["cosine"] = constraints.check_cosine(score.cosine)
    if not gates["cosine"].passed:
        return _rejected(skill_name, gates, fitness_score=score)

    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now if now is not None else time.time()))
    prop_dir = root / _PROPOSALS_DIRNAME / skill_name
    prop_dir.mkdir(parents=True, exist_ok=True)
    prop_path = prop_dir / f"{ts}.md"
    side_path = prop_dir / f"{ts}.json"
    prop_path.write_text(candidate, encoding="utf-8")
    side_payload = {
        "skill": skill_name,
        "created_at": ts,
        "fitness": {
            "judge": score.judge,
            "length_penalty": score.length_penalty,
            "cosine": score.cosine,
            "total": score.total,
        },
        "gates": {k: asdict(g) for k, g in gates.items()},
        "trajectories": [asdict(t) for t in trajectories],
    }
    side_path.write_text(json.dumps(side_payload, indent=2), encoding="utf-8")

    fitness_summary: dict[str, float] = {
        "judge": score.judge,
        "length_penalty": score.length_penalty,
        "cosine": score.cosine,
        "total": score.total,
    }
    return EvolveResult(
        skill_name=skill_name,
        approved=True,
        gates={k: asdict(g) for k, g in gates.items()},
        fitness=fitness_summary,
        proposal_path=str(prop_path),
        sidecar_path=str(side_path),
    )


# ---------- helpers ----------

def _run_gates(
    *,
    candidate: str,
    baseline: str,
    baseline_name: str,
    baseline_version: int | None,
    pytest_runner: constraints.PytestRunner | None,
) -> dict[str, constraints.GateResult]:
    del baseline  # unused in synchronous gates; cosine gate runs post-scoring.
    return {
        "size": constraints.check_size(candidate),
        "injection": constraints.check_injection(candidate),
        "frontmatter": constraints.check_frontmatter(
            candidate,
            baseline_name=baseline_name,
            baseline_version=baseline_version,
        ),
        "pytest": constraints.check_pytest(pytest_runner),
    }


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _rejected(
    skill_name: str,
    gates: dict[str, constraints.GateResult],
    *,
    fitness_score: fitness.FitnessScore | None,
) -> EvolveResult:
    failing = [g.name for g in gates.values() if not g.passed]
    return EvolveResult(
        skill_name=skill_name,
        approved=False,
        gates={k: asdict(g) for k, g in gates.items()},
        fitness=(
            {
                "judge": fitness_score.judge,
                "length_penalty": fitness_score.length_penalty,
                "cosine": fitness_score.cosine,
                "total": fitness_score.total,
            }
            if fitness_score is not None
            else None
        ),
        proposal_path=None,
        sidecar_path=None,
        reason=f"gates failed: {','.join(failing)}",
    )


def _failed(skill_name: str, code: str, reason: str) -> EvolveResult:
    return EvolveResult(
        skill_name=skill_name,
        approved=False,
        gates={},
        fitness=None,
        proposal_path=None,
        sidecar_path=None,
        reason=f"{code}: {reason}",
    )


__all__ = ["EvolveResult", "evolve"]
