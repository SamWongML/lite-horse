"""GEPA-style population loop entry-point.

Orchestrates the three other modules:

1. :func:`mine_eval_set_from_outcomes` (or its async cloud sibling) →
   list of :class:`EvalCase` we want each variant to handle.
2. For each of ``generations`` rounds:
   - :func:`generate_population` produces K mutated variants, seeded
     by the previous round's best (or the baseline on round 0).
   - :func:`diversity_prune` collapses near-duplicates.
   - :func:`score_variants` scores the survivors.
   - The frontier's top point becomes the next round's seed.
3. The final best variant is written out as a ``skill_proposals`` row
   in the cloud path, or to ``~/.litehorse/agents/<agent>/skills/.proposals/<slug>/<ts>.md``
   in the CLI path.

A cheap pre-flight cost estimator aborts the run before any model call
when the projected spend exceeds :data:`GEPA_COST_GATE_USD`.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lite_horse.constants import (
    GEPA_COST_GATE_USD,
    GEPA_DEFAULT_GENERATIONS,
    GEPA_DEFAULT_POPULATION_SIZE,
    GEPA_MIN_TRAJECTORIES,
)
from lite_horse.evolve.gepa.eval_set import EvalCase
from lite_horse.evolve.gepa.fitness import (
    Classifier,
    GepaFitness,
    RunCase,
    VariantScore,
    score_variants,
)
from lite_horse.evolve.gepa.population import (
    Embedder,
    VariantGenerator,
    diversity_prune,
    generate_population,
)


@dataclass
class GepaResult:
    """End-of-run state. Persisted by the caller to skill_proposals."""

    skill_slug: str
    accepted: bool
    best_skill_md: str | None
    best_rating: float
    best_size: int
    generations: list[dict[str, Any]] = field(default_factory=list)
    aborted_reason: str | None = None
    cost_estimate_usd: float = 0.0


# ---------- cost gate ----------

#: Rough token estimate per call (eyeballed; the gate is conservative).
_AVG_TOKENS_PER_VARIANT = 1_500
_AVG_TOKENS_PER_CASE_RUN = 2_500
_AVG_TOKENS_PER_CLASSIFICATION = 600
#: Mini-tier blended rate (input + output) per 1k tokens.
_BLENDED_USD_PER_1K_TOK = 0.001


def cost_estimate_usd(
    *,
    population_size: int,
    generations: int,
    case_count: int,
    per_1k_usd: float = _BLENDED_USD_PER_1K_TOK,
) -> float:
    """Pre-flight token estimate for one GEPA run.

    The math is intentionally crude — we only need to catch
    runaway-config requests before any model call. Per generation:

    * ``population_size`` variant generations
    * ``population_size * case_count`` agent runs
    * ``population_size * case_count`` classifier calls
    """
    per_gen_tokens = (
        population_size * _AVG_TOKENS_PER_VARIANT
        + population_size * case_count * _AVG_TOKENS_PER_CASE_RUN
        + population_size * case_count * _AVG_TOKENS_PER_CLASSIFICATION
    )
    total_tokens = per_gen_tokens * generations
    return (total_tokens / 1_000.0) * per_1k_usd


# ---------- main loop ----------


def run_gepa(
    *,
    skill_slug: str,
    baseline: str,
    cases: list[EvalCase],
    generator: VariantGenerator,
    embedder: Embedder,
    run_case: RunCase,
    classifier: Classifier,
    population_size: int = GEPA_DEFAULT_POPULATION_SIZE,
    generations: int = GEPA_DEFAULT_GENERATIONS,
    cost_gate_usd: float = GEPA_COST_GATE_USD,
    min_cases: int = GEPA_MIN_TRAJECTORIES,
) -> GepaResult:
    """Run the full N-generation loop. Pure-Python; no I/O.

    Returns a :class:`GepaResult`; the caller decides whether to
    write it back as a ``skill_proposals`` row (cloud) or a
    ``.proposals/<ts>.md`` file (CLI).
    """
    if len(cases) < min_cases:
        return GepaResult(
            skill_slug=skill_slug,
            accepted=False,
            best_skill_md=None,
            best_rating=0.0,
            best_size=len(baseline.encode("utf-8")),
            aborted_reason=(
                f"only {len(cases)} eval cases (< {min_cases})"
            ),
        )

    estimated_cost = cost_estimate_usd(
        population_size=population_size,
        generations=generations,
        case_count=len(cases),
    )
    if estimated_cost > cost_gate_usd:
        return GepaResult(
            skill_slug=skill_slug,
            accepted=False,
            best_skill_md=None,
            best_rating=0.0,
            best_size=len(baseline.encode("utf-8")),
            aborted_reason=(
                f"projected cost ${estimated_cost:.2f} > gate ${cost_gate_usd:.2f}"
            ),
            cost_estimate_usd=estimated_cost,
        )

    seed = baseline
    overall_best: VariantScore | None = None
    gens: list[dict[str, Any]] = []
    for gen in range(generations):
        population = generate_population(
            seed, generator=generator, size=population_size
        )
        population = diversity_prune(population, embedder=embedder)
        fitness: GepaFitness = score_variants(
            population,
            cases,
            run_case=run_case,
            classifier=classifier,
        )
        gens.append(
            {
                "generation": gen,
                "population_size": len(population),
                "frontier_size": len(fitness.frontier),
                "best_rating": fitness.best.mean_rating,
                "best_size": fitness.best.body_bytes,
            }
        )
        if overall_best is None or fitness.best.dominates(overall_best):
            overall_best = fitness.best
        seed = fitness.best.variant.skill_md

    assert overall_best is not None  # loop always runs at least once
    return GepaResult(
        skill_slug=skill_slug,
        accepted=True,
        best_skill_md=overall_best.variant.skill_md,
        best_rating=overall_best.mean_rating,
        best_size=overall_best.body_bytes,
        generations=gens,
        cost_estimate_usd=estimated_cost,
    )


# ---------- CLI sink ----------


def write_local_proposal(
    *,
    skill_slug: str,
    result: GepaResult,
    agent_skills_root: Path,
    now: float | None = None,
) -> tuple[Path, Path] | None:
    """Drop a GEPA winner into ``<agent>/skills/.proposals/<slug>/``.

    Returns ``(proposal_md_path, sidecar_json_path)`` on success, or
    ``None`` if the result is not accepted (cost-gated, too few cases,
    etc.). Mirrors the v0.2 ``.proposals`` shape so the existing CLI
    merge surface keeps working.
    """
    if not result.accepted or result.best_skill_md is None:
        return None
    ts = time.strftime(
        "%Y%m%dT%H%M%S", time.gmtime(now if now is not None else time.time())
    )
    prop_dir = agent_skills_root / ".proposals" / skill_slug
    prop_dir.mkdir(parents=True, exist_ok=True)
    prop_md = prop_dir / f"{ts}.md"
    prop_md.write_text(result.best_skill_md, encoding="utf-8")
    side_path = prop_dir / f"{ts}.json"
    payload = {
        "skill": skill_slug,
        "created_at": ts,
        "source": "gepa",
        "fitness": {
            "best_rating": result.best_rating,
            "best_size": result.best_size,
            "cost_estimate_usd": result.cost_estimate_usd,
        },
        "generations": result.generations,
    }
    side_path.write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    return prop_md, side_path


# ---------- helpers for cloud sink ----------


def result_as_fitness_jsonb(result: GepaResult) -> dict[str, Any]:
    """Pack a :class:`GepaResult` into the JSONB shape ``skill_proposals.fitness`` expects."""
    payload = asdict(result)
    payload.pop("best_skill_md", None)
    payload["source"] = "gepa"
    return payload


_RunCallable = Callable[[], GepaResult]
