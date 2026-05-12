"""Phase 33 acceptance — per-user agent build path.

Drives ``run_turn_streaming_for_user`` with the surrounding moving
parts (``db_session`` / ``_ensure_ready`` / ``Runner.run_streamed`` /
``build_agent_for_user``) stubbed out. Verifies that the engine
1) reads the user's effective config + memory documents,
2) feeds them into ``build_agent_for_user`` along with the resolved
   provider api key + GitHub token,
3) translates the SDK stream into the same ``StreamEvent`` shapes the
   SSE driver already understands, and
4) emits a terminal ``StreamDone``.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from lite_horse.api import StreamDelta, StreamDone, StreamToolCall
from lite_horse.web.turns import TurnRequest

pytestmark = pytest.mark.asyncio


# ---------- minimal SDK fakes ----------


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = input_tokens + output_tokens


class _FakeResponse:
    def __init__(self, usage: _FakeUsage) -> None:
        self.usage = usage


class _FakeRawResponseEvent:
    type = "raw_response_event"

    def __init__(self, data: Any) -> None:
        self.data = data


class _FakeOutputTextDelta:
    type = "response.output_text.delta"

    def __init__(self, delta: str) -> None:
        self.delta = delta


class _FakeResponseCompleted:
    type = "response.completed"

    def __init__(self, response: _FakeResponse) -> None:
        self.response = response


class _FakeRunItemStreamEvent:
    type = "run_item_stream_event"

    def __init__(self, item: Any) -> None:
        self.item = item


class _FakeStreaming:
    def __init__(self, events: list[Any], final_output: str) -> None:
        self._events = events
        self.final_output = final_output
        self.raw_responses = [object(), object()]

    async def stream_events(self):
        for ev in self._events:
            await asyncio.sleep(0)
            yield ev


# ---------- repo fakes returned from the patched db_session ----------


@dataclass
class _FakeUserSettings:
    default_model: str | None = "gpt-4o-mini"
    permission_mode: str = "auto"
    rate_limit_per_min: int | None = None
    cost_budget_usd_micro: int | None = None


class _FakeMemoryRepo:
    def __init__(self, _session: Any) -> None:
        pass

    async def get(self, kind: str) -> str:
        return {"memory.md": "MEM", "user.md": "PROFILE"}.get(kind, "")


class _FakeUserSettingsRepo:
    def __init__(self, _session: Any) -> None:
        pass

    async def get(self) -> _FakeUserSettings:
        return _FakeUserSettings()


class _FakeByo:
    def __init__(self, _session: Any, _kms: Any) -> None:
        pass

    async def get_key(self, name: str) -> str | None:
        return {"openai": "sk-byo-test", "github": "ghp_test"}.get(name)


class _FakeSessionSummaryRepo:
    def __init__(self, _session: Any) -> None:
        pass

    async def list_recent(self, **_kw: Any) -> list[Any]:
        return []


class _FakeSessionRepo:
    def __init__(self, _session: Any) -> None:
        pass

    async def get_session_meta(self, _session_id: str) -> None:
        return None


class _FakeAgentRow:
    """Phase 41 stub for ``AgentRepo.ensure_default()`` returning a row."""

    id = "00000000-0000-0000-0000-0000000000aa"
    slug = "default"
    name = "default"
    permission_mode = "auto"
    default_model: str | None = None
    archived_at = None


class _FakeAgentRepo:
    def __init__(self, _session: Any) -> None:
        pass

    async def ensure_default(self) -> _FakeAgentRow:
        return _FakeAgentRow()

    async def get(self, _agent_id: Any) -> _FakeAgentRow:
        return _FakeAgentRow()


class _FakeKms:
    pass


class _FakeMcpPool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def acquire(self, *, user_id: str, eff: Any) -> list[Any]:
        self.calls.append((user_id, eff))
        return []


@dataclass
class _Captured:
    build_agent_kwargs: dict[str, Any] | None = None


async def test_per_user_engine_invokes_build_agent_for_user(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _Captured()

    # Patch the moving parts the engine reaches for.
    import lite_horse.web.turn_engine as engine_mod

    @asynccontextmanager
    async def fake_db_session(user_id: str | None, agent_id: str | None = None):
        yield SimpleNamespace(_user_id=user_id, _agent_id=agent_id)

    monkeypatch.setattr(engine_mod, "db_session", fake_db_session)
    monkeypatch.setattr(engine_mod, "MemoryRepo", _FakeMemoryRepo)
    monkeypatch.setattr(engine_mod, "UserSettingsRepo", _FakeUserSettingsRepo)
    monkeypatch.setattr(engine_mod, "ByoKeyStore", _FakeByo)
    monkeypatch.setattr(engine_mod, "AgentRepo", _FakeAgentRepo)
    monkeypatch.setattr(engine_mod, "SessionSummaryRepo", _FakeSessionSummaryRepo)
    monkeypatch.setattr(engine_mod, "SessionRepo", _FakeSessionRepo)

    fake_eff = SimpleNamespace(mcp_servers=[])

    async def fake_get_or_compute(*_a: Any, **_kw: Any) -> Any:
        return fake_eff

    monkeypatch.setattr(
        engine_mod, "get_or_compute_effective_config", fake_get_or_compute
    )

    fake_local_db = object()
    fake_cfg = SimpleNamespace(
        model="gpt-4o-mini",
        agent=SimpleNamespace(max_turns=10),
    )

    async def fake_ensure_ready() -> tuple[Any, Any, Any]:
        return fake_local_db, object(), fake_cfg

    monkeypatch.setattr(engine_mod, "_ensure_ready", fake_ensure_ready)

    sentinel_agent = object()

    def fake_build_agent_for_user(**kwargs: Any) -> Any:
        captured.build_agent_kwargs = kwargs
        return sentinel_agent

    monkeypatch.setattr(
        engine_mod, "build_agent_for_user", fake_build_agent_for_user
    )

    class _FakeSDKSession:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

    monkeypatch.setattr(engine_mod, "SDKSession", _FakeSDKSession)

    streaming_events = [
        _FakeRawResponseEvent(_FakeOutputTextDelta("hi")),
        _FakeRawResponseEvent(
            _FakeResponseCompleted(
                _FakeResponse(_FakeUsage(input_tokens=11, output_tokens=3))
            )
        ),
    ]

    captured_runner_args: dict[str, Any] = {}

    def fake_run_streamed(agent: Any, text: str, **kw: Any) -> _FakeStreaming:
        captured_runner_args["agent"] = agent
        captured_runner_args["text"] = text
        captured_runner_args.update(kw)
        return _FakeStreaming(streaming_events, final_output="hi")

    monkeypatch.setattr(
        engine_mod.Runner, "run_streamed", staticmethod(fake_run_streamed)
    )

    pool = _FakeMcpPool()
    kms = _FakeKms()

    req = TurnRequest(
        user_id="00000000-0000-0000-0000-000000000001",
        session_key="sess-engine",
        text="hello world",
    )
    out = [
        ev
        async for ev in engine_mod.run_turn_streaming_for_user(
            req, mcp_pool=pool, kms=kms, redis=None
        )
    ]

    # Build-agent path was exercised with the per-user inputs.
    assert captured.build_agent_kwargs is not None
    kwargs = captured.build_agent_kwargs
    assert kwargs["user_id"] == req.user_id
    assert kwargs["memory_text"] == "MEM"
    assert kwargs["user_md_text"] == "PROFILE"
    assert kwargs["api_key"] == "sk-byo-test"
    assert kwargs["github_token"] == "ghp_test"
    assert kwargs["model_override"] == "gpt-4o-mini"
    assert kwargs["mcp_servers"] == []
    assert kwargs["eff"] is fake_eff
    assert kwargs["config"] is fake_cfg

    # MCP pool called against the request's user_id.
    assert pool.calls == [(req.user_id, fake_eff)]

    # The SDK Runner saw the agent the factory returned and the user text.
    assert captured_runner_args["agent"] is sentinel_agent
    assert captured_runner_args["text"] == req.text

    # Stream surface matches the SSE driver's expectations.
    deltas = [ev for ev in out if isinstance(ev, StreamDelta)]
    dones = [ev for ev in out if isinstance(ev, StreamDone)]
    assert [d.text for d in deltas] == ["hi"]
    assert len(dones) == 1
    done = dones[0]
    assert done.result.final_output == "hi"
    assert done.result.input_tokens == 11
    assert done.result.output_tokens == 3
    assert done.result.session_key == req.session_key
    # No tool-call events were scripted.
    assert not [ev for ev in out if isinstance(ev, StreamToolCall)]
