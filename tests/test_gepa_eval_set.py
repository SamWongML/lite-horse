"""Hermetic mine_eval_set_from_outcomes shape tests."""
from __future__ import annotations

from lite_horse.evolve.gepa.eval_set import (
    EvalCase,
    mine_eval_set_from_outcomes,
)


def _row(rating: int = 1, request: str = "do thing", reason: str | None = "r") -> dict:
    return {
        "turn_id": f"t-{rating}",
        "session_id": f"s-{rating}",
        "rating": rating,
        "reason": reason,
        "user_request": request,
    }


def test_skips_neutral_ratings() -> None:
    cases = mine_eval_set_from_outcomes(
        [_row(rating=0), _row(rating=1), _row(rating=-1)],
        min_cases=1,
        max_cases=10,
    )
    assert all(c.rating != 0 for c in cases)
    assert len(cases) == 2


def test_skips_rows_without_user_request() -> None:
    cases = mine_eval_set_from_outcomes(
        [_row(rating=1, request="   "), _row(rating=1, request="real")],
        min_cases=1,
        max_cases=10,
    )
    assert [c.user_request for c in cases] == ["real"]


def test_respects_max_cases() -> None:
    rows = [_row(rating=1, request=f"r{i}") for i in range(20)]
    cases = mine_eval_set_from_outcomes(rows, min_cases=1, max_cases=5)
    assert len(cases) == 5
    assert all(isinstance(c, EvalCase) for c in cases)
