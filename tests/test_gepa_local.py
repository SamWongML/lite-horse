"""Deterministic local stubs for the GEPA loop."""
from __future__ import annotations

from lite_horse.evolve.gepa.eval_set import EvalCase
from lite_horse.evolve.gepa.local import (
    local_classifier,
    local_embedder,
    local_generator,
    local_run_case,
)


def test_local_generator_appends_mutation_hint_comment() -> None:
    gen = local_generator()
    out = gen("# title\n\nbody.", "rephrase")
    assert "rephrase" in out
    assert out.startswith("# title")


def test_local_embedder_is_27_bin_normalised() -> None:
    emb = local_embedder()
    vec = emb("hello world")
    assert len(vec) == 27
    assert abs(sum(vec) - 1.0) < 1e-9


def test_local_run_case_smuggles_rating_into_text() -> None:
    rc = local_run_case()
    case = EvalCase(
        turn_id="t",
        user_request="hi there",
        rating=-1,
        reason="bad",
        session_id="s",
    )
    text = rc("variant body", case)
    assert "hi there" in text
    cls = local_classifier()
    assert cls("hi there", text, "bad") == -1


def test_local_classifier_clamps_unknown_to_zero() -> None:
    assert local_classifier()("q", "no marker here", "r") == 0
