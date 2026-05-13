"""GEPA-style population-level skill evolution — Phase 45.

Replaces the single-shot reflection in :mod:`lite_horse.evolve.runner`
with an N-generation loop over a K-member population of mutated
``SKILL.md`` variants. Each generation is scored by replaying a mined
eval set through the agent and grading turn outcomes with the Phase 44
classifier. The winning variant on the rating x size Pareto frontier
is emitted as a ``skill_proposals`` row.

This package is **opt-in per skill** via ``frontmatter: gepa: true`` —
the scheduler only enqueues weekly runs for skills that flip that bit.
"""

from lite_horse.evolve.gepa.eval_set import EvalCase, mine_eval_set
from lite_horse.evolve.gepa.fitness import (
    GepaFitness,
    VariantScore,
    score_variants,
)
from lite_horse.evolve.gepa.population import (
    Variant,
    diversity_prune,
    generate_population,
)
from lite_horse.evolve.gepa.runner import (
    GepaResult,
    cost_estimate_usd,
    run_gepa,
)

__all__ = [
    "EvalCase",
    "GepaFitness",
    "GepaResult",
    "Variant",
    "VariantScore",
    "cost_estimate_usd",
    "diversity_prune",
    "generate_population",
    "mine_eval_set",
    "run_gepa",
    "score_variants",
]
