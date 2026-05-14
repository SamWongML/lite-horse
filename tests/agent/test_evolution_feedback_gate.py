"""``EvolutionHook._should_refine`` honors ``FeedbackSink`` stats.

Marker-only behaviour stays the prior default; once a viewed skill has
crossed ``CURATOR_REFINE_MIN_OUTCOMES`` we let aggregate ratings flip
the decision: positive trend suppresses refinement, negative trend
triggers it without an in-trajectory error.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from lite_horse.agent import evolution as evo
from lite_horse.agent.backends.feedback import OutcomeStats
from lite_horse.agent.evolution import EvolutionHook
from lite_horse.constants import CURATOR_REFINE_MIN_OUTCOMES


@dataclass
class _Tenant:
    feedback: Any


class _StubFeedback:
    def __init__(self, stats: OutcomeStats) -> None:
        self._stats = stats
        self.calls: list[str] = []

    async def rating_stats(self, *, skill_slug: str) -> OutcomeStats:
        self.calls.append(skill_slug)
        return self._stats


class _RaisingFeedback:
    async def rating_stats(self, *, skill_slug: str) -> OutcomeStats:
        raise RuntimeError("simulated DB outage")


def _ctx_with(feedback: Any, monkeypatch: pytest.MonkeyPatch | None = None) -> Any:
    tenant = _Tenant(feedback=feedback)
    if monkeypatch is not None:
        monkeypatch.setattr(evo, "resolve_tenant", lambda _ctx: tenant)

    class _Ctx:
        context = tenant

    return _Ctx()


@pytest.mark.asyncio
async def test_no_viewed_skill_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    hook = EvolutionHook(model="test")
    hook._viewed_skills = []
    assert await hook._should_refine(_ctx_with(None, monkeypatch)) is False


@pytest.mark.asyncio
async def test_marker_only_below_outcome_floor_still_refines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hook = EvolutionHook(model="test")
    hook._viewed_skills = ["x"]
    hook._error_summary = "traceback"
    feedback = _StubFeedback(OutcomeStats(use_count=0, success_count=0, error_count=0))
    assert await hook._should_refine(_ctx_with(feedback, monkeypatch)) is True


@pytest.mark.asyncio
async def test_negative_history_triggers_refine_without_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hook = EvolutionHook(model="test")
    hook._viewed_skills = ["x"]
    hook._error_summary = None  # no in-trajectory error
    feedback = _StubFeedback(
        OutcomeStats(
            use_count=CURATOR_REFINE_MIN_OUTCOMES * 2,
            success_count=1,
            error_count=CURATOR_REFINE_MIN_OUTCOMES + 1,
        )
    )
    assert await hook._should_refine(_ctx_with(feedback, monkeypatch)) is True


@pytest.mark.asyncio
async def test_positive_history_suppresses_refine_despite_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hook = EvolutionHook(model="test")
    hook._viewed_skills = ["x"]
    hook._error_summary = "exception:"
    feedback = _StubFeedback(
        OutcomeStats(
            use_count=CURATOR_REFINE_MIN_OUTCOMES * 2,
            success_count=CURATOR_REFINE_MIN_OUTCOMES + 1,
            error_count=1,
        )
    )
    assert await hook._should_refine(_ctx_with(feedback, monkeypatch)) is False


@pytest.mark.asyncio
async def test_feedback_outage_falls_back_to_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hook = EvolutionHook(model="test")
    hook._viewed_skills = ["x"]
    hook._error_summary = "failed"
    feedback = _RaisingFeedback()
    ctx = _ctx_with(feedback, monkeypatch)
    assert await hook._should_refine(ctx) is True

    hook._error_summary = None
    assert await hook._should_refine(ctx) is False
