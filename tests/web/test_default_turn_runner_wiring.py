"""The cloud route uses ``run_turn_streaming_for_user``.

Verifies the dependency-injected default runner closes over app state
(``mcp_pool`` / ``kms`` / ``redis``) and forwards a ``TurnRequest`` into
:func:`lite_horse.web.turn_engine.run_turn_streaming_for_user`. The
engine itself is monkey-patched so the test stays free of Postgres,
KMS, and the OpenAI SDK.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from lite_horse.web.routes.turns import get_turn_runner
from lite_horse.web.turns import TurnRequest

pytestmark = pytest.mark.asyncio


@dataclass
class _Captured:
    req: TurnRequest | None = None
    mcp_pool: object | None = None
    kms: object | None = None
    redis: object | None = None


async def test_default_runner_forwards_to_run_turn_streaming_for_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _Captured()

    async def fake_engine(
        req: TurnRequest,
        *,
        mcp_pool: object,
        kms: object,
        redis: object,
    ) -> AsyncIterator[Any]:
        captured.req = req
        captured.mcp_pool = mcp_pool
        captured.kms = kms
        captured.redis = redis
        yield SimpleNamespace(
            __class__=type("StreamDelta", (), {}), text="ok"
        )

    import lite_horse.web.turn_engine as engine_mod

    monkeypatch.setattr(
        engine_mod, "run_turn_streaming_for_user", fake_engine
    )

    sentinel_pool = object()
    sentinel_kms = object()
    sentinel_redis = object()
    fake_state = SimpleNamespace(
        mcp_pool=sentinel_pool,
        kms=sentinel_kms,
        redis=sentinel_redis,
        turn_runner=None,
    )
    fake_request = SimpleNamespace(app=SimpleNamespace(state=fake_state))

    runner = get_turn_runner(fake_request)  # type: ignore[arg-type]
    req = TurnRequest(
        user_id="user-abc",
        session_key="sess-xyz",
        text="hello",
    )
    out = [ev async for ev in runner(req)]

    assert len(out) == 1
    assert captured.req is req
    assert captured.mcp_pool is sentinel_pool
    assert captured.kms is sentinel_kms
    assert captured.redis is sentinel_redis


async def test_state_override_takes_precedence() -> None:
    """If app.state.turn_runner is set (test-injection), the default isn't used."""
    sentinel_calls = 0

    async def explicit_runner(_req: TurnRequest) -> AsyncIterator[Any]:
        nonlocal sentinel_calls
        sentinel_calls += 1
        if False:
            yield  # make this a generator without emitting

    fake_state = SimpleNamespace(
        mcp_pool=None, kms=None, redis=None, turn_runner=explicit_runner
    )
    fake_request = SimpleNamespace(app=SimpleNamespace(state=fake_state))

    runner = get_turn_runner(fake_request)  # type: ignore[arg-type]
    req = TurnRequest(user_id="u", session_key="s", text="t")
    _ = [ev async for ev in runner(req)]
    assert sentinel_calls == 1
