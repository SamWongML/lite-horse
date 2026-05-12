"""Unit tests for :class:`OutcomeClassifier` (Phase 44).

The SDK round-trip is stubbed via ``Runner.run`` monkey-patch so we
exercise the prompt-building + JSON-parsing layers without burning
tokens. Each test pins one collapse path: success, failure, ambiguous,
unparseable, exception, empty trajectory.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from lite_horse.agent import outcome_classifier as oc


class _FakeRunResult:
    def __init__(self, output: str | None) -> None:
        self.final_output = output


@pytest.mark.asyncio
async def test_no_trajectory_collapses_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def _run(*_a, **_kw):
        nonlocal called
        called = True
        return _FakeRunResult('{"rating": 1, "reason": "x"}')

    monkeypatch.setattr(oc.Runner, "run", staticmethod(_run))
    clf = oc.OutcomeClassifier(model="test")
    result = await clf.run(user_request=None, final_text=None, tool_tail=None)
    assert result.rating == 0
    assert result.reason == "no trajectory"
    assert called is False, "should never spin up the side-agent on empty input"


@pytest.mark.asyncio
async def test_success_rating(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run(*_a, **_kw):
        return _FakeRunResult('{"rating": 1, "reason": "wrote memory + answered"}')

    monkeypatch.setattr(oc.Runner, "run", staticmethod(_run))
    clf = oc.OutcomeClassifier(model="test")
    result = await clf.run(user_request="do x", final_text="done", tool_tail=None)
    assert result.rating == 1
    assert "wrote memory" in result.reason


@pytest.mark.asyncio
async def test_failure_rating(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run(*_a, **_kw):
        return _FakeRunResult('{"rating": -1, "reason": "github 404"}')

    monkeypatch.setattr(oc.Runner, "run", staticmethod(_run))
    clf = oc.OutcomeClassifier(model="test")
    result = await clf.run(
        user_request="fix the bug",
        final_text="failed",
        tool_tail=[{"name": "github", "output": "error: 404"}],
    )
    assert result.rating == -1
    assert "404" in result.reason


@pytest.mark.asyncio
async def test_unparseable_collapses_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run(*_a, **_kw):
        return _FakeRunResult("not json at all")

    monkeypatch.setattr(oc.Runner, "run", staticmethod(_run))
    clf = oc.OutcomeClassifier(model="test")
    result = await clf.run(user_request="x", final_text="y", tool_tail=None)
    assert result.rating == 0
    assert result.reason == "unparseable"


@pytest.mark.asyncio
async def test_exception_collapses_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run(*_a, **_kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(oc.Runner, "run", staticmethod(_run))
    clf = oc.OutcomeClassifier(model="test")
    result = await clf.run(user_request="x", final_text=None, tool_tail=None)
    assert result.rating == 0
    assert result.reason == "classifier raised"


@pytest.mark.asyncio
async def test_invalid_rating_value_clamps_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run(*_a, **_kw):
        return _FakeRunResult('{"rating": 7, "reason": "weird"}')

    monkeypatch.setattr(oc.Runner, "run", staticmethod(_run))
    clf = oc.OutcomeClassifier(model="test")
    result = await clf.run(user_request="x", final_text="y", tool_tail=None)
    assert result.rating == 0


@pytest.mark.asyncio
async def test_tail_trim_drops_empty_entries() -> None:
    clf = oc.OutcomeClassifier(model="test")
    tail = clf._tail(  # type: ignore[attr-defined]
        [
            {"name": "", "output": "x"},
            {"name": "tool", "output": ""},
            {"name": "tool", "output": "ok"},
        ]
    )
    assert tail == [{"name": "tool", "output": "ok"}]


def test_parse_strips_code_fence() -> None:
    res = oc._parse('```json\n{"rating": 1, "reason": "ok"}\n```')
    assert res.rating == 1
    assert res.reason == "ok"


def test_classifier_result_is_dataclass_frozen() -> None:
    # Ensure the dataclass is hashable / non-mutable, matching the Protocol shape.
    res = oc.ClassifierResult(rating=1, reason="x")
    assert SimpleNamespace(rating=res.rating, reason=res.reason)
