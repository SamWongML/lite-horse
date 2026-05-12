"""Phase 43 summarizer side-agent tests."""
from __future__ import annotations

import json
from typing import Any

import pytest

from lite_horse.agent import summarizer as summ_mod
from lite_horse.agent.summarizer import (
    SUMMARIZER_MAX_MESSAGES,
    SUMMARIZER_MAX_SUMMARY_CHARS,
    SUMMARIZER_MAX_TOPIC_CHARS,
    SessionSummary,
    Summarizer,
    _parse_summary,
)


class _FakeRunResult:
    def __init__(self, final_output: str) -> None:
        self.final_output = final_output


def _stub_runner(
    monkeypatch: pytest.MonkeyPatch, *, final_output: str
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_run(agent: Any, prompt: str, **kwargs: Any) -> _FakeRunResult:
        calls.append({"agent": agent, "prompt": prompt, "kwargs": kwargs})
        return _FakeRunResult(final_output=final_output)

    monkeypatch.setattr(summ_mod.Runner, "run", fake_run)
    return calls


# ---------- _parse_summary ----------


def test_parse_valid_payload() -> None:
    raw = json.dumps({"topic": "deploy", "summary": "user shipped v0.5."})
    assert _parse_summary(raw) == SessionSummary(
        topic="deploy", summary="user shipped v0.5."
    )


def test_parse_strips_fenced_block() -> None:
    raw = '```json\n{"topic": "a", "summary": "b"}\n```'
    assert _parse_summary(raw) == SessionSummary(topic="a", summary="b")


def test_parse_rejects_non_object() -> None:
    assert _parse_summary("[]") == SessionSummary(topic="", summary="")


def test_parse_rejects_invalid_json() -> None:
    assert _parse_summary("not json at all") == SessionSummary(
        topic="", summary=""
    )


def test_parse_truncates_to_caps() -> None:
    over_topic = "x" * (SUMMARIZER_MAX_TOPIC_CHARS + 50)
    over_summary = "y" * (SUMMARIZER_MAX_SUMMARY_CHARS + 50)
    raw = json.dumps({"topic": over_topic, "summary": over_summary})
    out = _parse_summary(raw)
    assert len(out.topic) == SUMMARIZER_MAX_TOPIC_CHARS
    assert len(out.summary) == SUMMARIZER_MAX_SUMMARY_CHARS


def test_parse_missing_fields_returns_empty() -> None:
    raw = json.dumps({"topic": "only-topic"})
    assert _parse_summary(raw) == SessionSummary(topic="only-topic", summary="")


# ---------- Summarizer.run ----------


@pytest.mark.asyncio
async def test_run_returns_parsed_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runner(
        monkeypatch,
        final_output=json.dumps(
            {"topic": "Q3 plan", "summary": "user discussed Q3 deploys."}
        ),
    )
    s = Summarizer(model="gpt-test")
    out = await s.run(
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    )
    assert out == SessionSummary(
        topic="Q3 plan", summary="user discussed Q3 deploys."
    )


@pytest.mark.asyncio
async def test_run_with_no_substantive_messages_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_runner(monkeypatch, final_output='{"topic":"x","summary":"y"}')
    s = Summarizer(model="gpt-test")
    out = await s.run(messages=[{"role": "tool", "content": "noise"}])
    assert out == SessionSummary(topic="", summary="")
    assert calls == []  # side-agent never invoked


@pytest.mark.asyncio
async def test_run_tail_caps_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_runner(monkeypatch, final_output='{"topic":"","summary":""}')
    s = Summarizer(model="gpt-test")
    msgs = [
        {"role": "user" if i % 2 else "assistant", "content": f"m{i}"}
        for i in range(SUMMARIZER_MAX_MESSAGES * 2)
    ]
    await s.run(messages=msgs)
    payload = json.loads(calls[0]["prompt"])
    assert len(payload["messages"]) == SUMMARIZER_MAX_MESSAGES


@pytest.mark.asyncio
async def test_run_side_agent_exception_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(*a: Any, **k: Any) -> _FakeRunResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(summ_mod.Runner, "run", fake_run)
    s = Summarizer(model="gpt-test")
    out = await s.run(messages=[{"role": "user", "content": "hi"}])
    assert out == SessionSummary(topic="", summary="")


@pytest.mark.asyncio
async def test_run_passes_max_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_runner(monkeypatch, final_output='{"topic":"","summary":""}')
    s = Summarizer(model="gpt-test", max_turns=5)
    await s.run(messages=[{"role": "user", "content": "hi"}])
    assert calls[0]["kwargs"].get("max_turns") == 5
