"""Score GEPA variants against the eval set.

For each variant, replay every :class:`EvalCase` through an injected
``run_case`` callable, score the response via the Phase 44 outcome
classifier, and aggregate into ``(mean_rating, variance, body_bytes)``.

``run_case`` is intentionally injectable: in production the worker
plumbs in :class:`lite_horse.agent.factory.build_agent_for_user` →
``Runner.run``; tests stub it with a fixture so the loop is hermetic.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from lite_horse.evolve.gepa.eval_set import EvalCase
from lite_horse.evolve.gepa.population import Variant

RunCase = Callable[[str, EvalCase], str]
"""``(variant_skill_md, eval_case) -> agent_final_text``."""

Classifier = Callable[[str, str, str], int]
"""``(user_request, agent_text, expected_reason) -> rating in {-1, 0, 1}``."""


@dataclass(frozen=True)
class VariantScore:
    """Aggregate score across the eval set for one variant."""

    variant: Variant
    mean_rating: float
    variance: float
    body_bytes: int
    per_case: tuple[int, ...]

    def dominates(self, other: VariantScore) -> bool:
        """Pareto-dominance on (rating ↑, size ↓)."""
        rating_better = self.mean_rating > other.mean_rating
        size_better = self.body_bytes < other.body_bytes
        rating_eq = math.isclose(self.mean_rating, other.mean_rating)
        size_eq = self.body_bytes == other.body_bytes
        if rating_better and (size_better or size_eq):
            return True
        if size_better and (rating_better or rating_eq):
            return True
        return False


@dataclass(frozen=True)
class GepaFitness:
    """The full scoring output for one generation."""

    scores: tuple[VariantScore, ...]
    frontier: tuple[VariantScore, ...]
    best: VariantScore

    @property
    def best_skill_md(self) -> str:
        return self.best.variant.skill_md


def score_variants(
    variants: list[Variant],
    cases: list[EvalCase],
    *,
    run_case: RunCase,
    classifier: Classifier,
) -> GepaFitness:
    """Score each variant against the full eval set.

    Empty case list -> every variant scores ``0.0`` and the baseline
    wins by size. The classifier is expected to map noisy text to a
    rating in ``{-1, 0, 1}``; the implementation typically wraps Phase
    44's :class:`OutcomeClassifier`.
    """
    if not variants:
        raise ValueError("score_variants requires at least one variant")
    scores: list[VariantScore] = []
    for v in variants:
        ratings: list[int] = []
        for case in cases:
            try:
                final_text = run_case(v.skill_md, case)
            except Exception:
                ratings.append(-1)
                continue
            try:
                rating = int(classifier(case.user_request, final_text, case.reason or ""))
            except Exception:
                rating = 0
            ratings.append(max(-1, min(1, rating)))
        if ratings:
            mean = sum(ratings) / len(ratings)
            var = sum((r - mean) ** 2 for r in ratings) / len(ratings)
        else:
            mean = 0.0
            var = 0.0
        scores.append(
            VariantScore(
                variant=v,
                mean_rating=mean,
                variance=var,
                body_bytes=len(v.skill_md.encode("utf-8")),
                per_case=tuple(ratings),
            )
        )

    frontier = _pareto_frontier(scores)
    # Pick the frontier point with the highest rating; break ties by
    # smaller body. The baseline (rating ~ 0) wins on a tie when every
    # mutation also lands at 0 because it tends to be the shortest
    # well-formed body in the seed set.
    best = max(
        frontier,
        key=lambda s: (s.mean_rating, -s.body_bytes),
    )
    return GepaFitness(
        scores=tuple(scores), frontier=tuple(frontier), best=best
    )


def _pareto_frontier(scores: list[VariantScore]) -> list[VariantScore]:
    """Return non-dominated members of the population."""
    out: list[VariantScore] = []
    for s in scores:
        dominated = False
        for other in scores:
            if other is s:
                continue
            if other.dominates(s):
                dominated = True
                break
        if not dominated:
            out.append(s)
    return out
