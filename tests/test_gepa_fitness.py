"""Phase 45: score_variants + Pareto frontier."""
from __future__ import annotations

import pytest

from lite_horse.evolve.gepa.eval_set import EvalCase
from lite_horse.evolve.gepa.fitness import (
    GepaFitness,
    VariantScore,
    score_variants,
)
from lite_horse.evolve.gepa.population import Variant


def _case(req: str = "do", rating: int = 1) -> EvalCase:
    return EvalCase(
        turn_id="t",
        user_request=req,
        rating=rating,
        reason="r",
        session_id="s",
    )


def test_score_variants_rejects_empty_population() -> None:
    with pytest.raises(ValueError):
        score_variants(
            [],
            [_case()],
            run_case=lambda _v, _c: "ok",
            classifier=lambda _r, _a, _e: 1,
        )


def test_score_variants_higher_classifier_wins() -> None:
    v1 = Variant(skill_md="short", mutation="baseline")
    v2 = Variant(skill_md="longer text here", mutation="m1")
    cases = [_case("a"), _case("b")]

    def run_case(variant_md: str, _c: EvalCase) -> str:
        return f"OK::{variant_md}"

    def classifier(_req: str, agent_text: str, _e: str) -> int:
        # The "longer" variant scores +1 every time, the short one scores 0.
        return 1 if "longer" in agent_text else 0

    fit: GepaFitness = score_variants(
        [v1, v2], cases, run_case=run_case, classifier=classifier
    )
    assert fit.best.variant is v2
    assert fit.best.mean_rating == 1.0


def test_score_variants_ties_break_by_smaller_body() -> None:
    v_short = Variant(skill_md="short", mutation="baseline")
    v_long = Variant(skill_md="much longer body here", mutation="m1")
    fit = score_variants(
        [v_short, v_long],
        [_case()],
        run_case=lambda _v, _c: "OK",
        classifier=lambda *_a: 0,  # equal rating
    )
    # Both score 0.0 → frontier on size; shorter wins the tiebreaker.
    assert fit.best.variant is v_short


def test_score_variants_classifier_exception_counts_zero() -> None:
    v = Variant(skill_md="b", mutation="baseline")

    def cls(_r: str, _a: str, _e: str) -> int:
        raise RuntimeError("boom")

    fit = score_variants(
        [v], [_case()], run_case=lambda _v, _c: "OK", classifier=cls
    )
    assert fit.best.mean_rating == 0.0


def test_score_variants_run_case_exception_counts_minus_one() -> None:
    v = Variant(skill_md="b", mutation="baseline")

    def run_case(_v: str, _c: EvalCase) -> str:
        raise RuntimeError("boom")

    fit = score_variants(
        [v], [_case()], run_case=run_case, classifier=lambda *_a: 1
    )
    assert fit.best.mean_rating == -1.0


def test_variant_score_dominates_is_pareto_on_rating_size() -> None:
    v = Variant(skill_md="x", mutation="m")
    better = VariantScore(
        variant=v,
        mean_rating=0.5,
        variance=0.0,
        body_bytes=100,
        per_case=(1,),
    )
    worse = VariantScore(
        variant=v,
        mean_rating=0.0,
        variance=0.0,
        body_bytes=200,
        per_case=(0,),
    )
    assert better.dominates(worse)
    assert not worse.dominates(better)
