"""run_gepa pre-flight gates + happy-path loop."""
from __future__ import annotations

from pathlib import Path

from lite_horse.evolve.gepa.eval_set import EvalCase
from lite_horse.evolve.gepa.local import (
    local_classifier,
    local_embedder,
    local_generator,
    local_run_case,
)
from lite_horse.evolve.gepa.runner import (
    GepaResult,
    cost_estimate_usd,
    result_as_fitness_jsonb,
    run_gepa,
    write_local_proposal,
)


def _cases(n: int) -> list[EvalCase]:
    return [
        EvalCase(
            turn_id=f"t{i}",
            user_request=f"req{i}",
            rating=1 if i % 2 == 0 else -1,
            reason="r",
            session_id=f"s{i}",
        )
        for i in range(n)
    ]


def test_cost_estimate_scales_with_inputs() -> None:
    small = cost_estimate_usd(
        population_size=2, generations=1, case_count=2
    )
    big = cost_estimate_usd(
        population_size=8, generations=4, case_count=10
    )
    assert big > small > 0


def test_run_gepa_aborts_when_cases_below_min() -> None:
    result = run_gepa(
        skill_slug="demo",
        baseline="BASE",
        cases=_cases(2),
        generator=local_generator(),
        embedder=local_embedder(),
        run_case=local_run_case(),
        classifier=local_classifier(),
        population_size=2,
        generations=1,
        min_cases=5,
    )
    assert not result.accepted
    assert result.aborted_reason is not None
    assert "eval cases" in result.aborted_reason


def test_run_gepa_aborts_when_cost_exceeds_gate() -> None:
    result = run_gepa(
        skill_slug="demo",
        baseline="BASE",
        cases=_cases(20),
        generator=local_generator(),
        embedder=local_embedder(),
        run_case=local_run_case(),
        classifier=local_classifier(),
        population_size=8,
        generations=3,
        cost_gate_usd=0.000_01,
        min_cases=1,
    )
    assert not result.accepted
    assert result.aborted_reason is not None
    assert "projected cost" in result.aborted_reason


def test_run_gepa_happy_path_returns_accepted_result() -> None:
    result = run_gepa(
        skill_slug="demo",
        baseline="# baseline\n\nbody\n",
        cases=_cases(2),
        generator=local_generator(),
        embedder=local_embedder(),
        run_case=local_run_case(),
        classifier=local_classifier(),
        population_size=3,
        generations=2,
        min_cases=1,
    )
    assert result.accepted
    assert result.best_skill_md is not None
    assert result.best_size > 0
    assert len(result.generations) == 2
    assert all(
        {"generation", "population_size", "frontier_size", "best_rating"}
        <= set(gen)
        for gen in result.generations
    )


def test_result_as_fitness_jsonb_drops_body_and_tags_source() -> None:
    result = GepaResult(
        skill_slug="x",
        accepted=True,
        best_skill_md="big body",
        best_rating=0.9,
        best_size=42,
    )
    payload = result_as_fitness_jsonb(result)
    assert "best_skill_md" not in payload
    assert payload["source"] == "gepa"
    assert payload["best_rating"] == 0.9


def test_write_local_proposal_skips_when_not_accepted(tmp_path: Path) -> None:
    result = GepaResult(
        skill_slug="x",
        accepted=False,
        best_skill_md=None,
        best_rating=0.0,
        best_size=0,
        aborted_reason="too few",
    )
    assert (
        write_local_proposal(
            skill_slug="x",
            result=result,
            agent_skills_root=tmp_path,
        )
        is None
    )


def test_write_local_proposal_emits_md_and_sidecar(tmp_path: Path) -> None:
    result = GepaResult(
        skill_slug="demo",
        accepted=True,
        best_skill_md="# demo\n\nbody\n",
        best_rating=0.5,
        best_size=14,
        generations=[{"generation": 0, "best_rating": 0.5}],
        cost_estimate_usd=0.0,
    )
    paths = write_local_proposal(
        skill_slug="demo",
        result=result,
        agent_skills_root=tmp_path,
        now=1_700_000_000.0,
    )
    assert paths is not None
    md, side = paths
    assert md.exists() and side.exists()
    assert md.parent == tmp_path / ".proposals" / "demo"
    assert "# demo" in md.read_text(encoding="utf-8")
    assert '"source": "gepa"' in side.read_text(encoding="utf-8")
