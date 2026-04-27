"""SSE streaming for ``POST /v1/turns:stream``.

Uses a fake :func:`turn_runner` that yields a deterministic event
sequence so the test does not boot the agent runtime. JWT verification
and lazy user-row creation are stubbed exactly like ``test_auth.py``.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

from lite_horse.web import auth as auth_mod
from lite_horse.web import create_app
from lite_horse.web.permissions import PermissionBroker
from lite_horse.web.routes.turns import (
    get_session_lock,
    get_turn_runner,
)
from lite_horse.web.turns import TurnRegistry, TurnRequest

pytestmark = pytest.mark.asyncio

_SECRET = b"phase35-test-secret-do-not-use-in-prod"
_KID = "phase35-kid"
_AUDIENCE = "lite-horse"
_ISSUER = "http://localhost:9999"


# ---------- fake stream events (duck-typed by the SSE translator) ----------


@dataclass
class _FakeDelta:
    text: str

    def __post_init__(self) -> None:
        self.__class__.__name__ = "StreamDelta"


@dataclass
class _FakeToolCall:
    name: str
    arguments: str
    id: str

    def __post_init__(self) -> None:
        self.__class__.__name__ = "StreamToolCall"


@dataclass
class _FakeToolOutput:
    name: str
    output: str
    id: str

    def __post_init__(self) -> None:
        self.__class__.__name__ = "StreamToolOutput"


@dataclass
class _FakeRunResult:
    final_output: str
    input_tokens: int = 12
    output_tokens: int = 7


@dataclass
class _FakeDone:
    result: _FakeRunResult

    def __post_init__(self) -> None:
        self.__class__.__name__ = "StreamDone"


# Pin __class__.__name__ at the class level (dataclasses set per instance).
class StreamDelta(_FakeDelta):
    pass


class StreamToolCall(_FakeToolCall):
    pass


class StreamToolOutput(_FakeToolOutput):
    pass


class StreamDone(_FakeDone):
    pass


# ---------- jwks / auth stubs ----------


def _jwks() -> dict[str, Any]:
    return {
        "keys": [
            {
                "kty": "oct",
                "kid": _KID,
                "alg": "HS256",
                "k": base64.urlsafe_b64encode(_SECRET).rstrip(b"=").decode("ascii"),
            }
        ]
    }


def _mint(sub: str = "phase35-user", role: str = "user") -> str:
    return jwt.encode(
        {
            "sub": sub,
            "role": role,
            "aud": _AUDIENCE,
            "iss": _ISSUER,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        },
        _SECRET,
        algorithm="HS256",
        headers={"kid": _KID},
    )


@pytest.fixture(autouse=True)
def _stub_jwks_and_user(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_jwks(_url: str) -> dict[str, Any]:
        return _jwks()

    fixed_uid = str(uuid.uuid4())

    async def fake_lookup(_external_id: str, _role: str) -> str:
        return fixed_uid

    monkeypatch.setattr(auth_mod, "_get_jwks", fake_jwks)
    monkeypatch.setattr(auth_mod, "_lookup_or_create_user", fake_lookup)
    auth_mod._clear_jwks_cache()


# ---------- shared fakes ----------


def _make_runner(events: list[Any]):
    async def runner(_req: TurnRequest) -> AsyncIterator[Any]:
        for ev in events:
            yield ev

    return runner


def _scripted_events(*, final: str = "hello world") -> list[Any]:
    return [
        StreamDelta(text="hello "),
        StreamToolCall(name="memory", arguments='{"op":"read"}', id="tc_1"),
        StreamToolOutput(name="memory", output="(empty)", id="tc_1"),
        StreamDelta(text="world"),
        StreamDone(result=_FakeRunResult(final_output=final)),
    ]


def _build_app(*, runner) -> Any:
    app = create_app()
    app.dependency_overrides[get_turn_runner] = lambda: runner
    app.dependency_overrides[get_session_lock] = lambda: None
    # Each test gets its own registry + broker so state is isolated.
    app.state.turn_registry = TurnRegistry()
    app.state.permission_broker = PermissionBroker(redis=None)
    return app


def _parse_sse(raw: bytes) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    name = ""
    data_lines: list[str] = []
    text = raw.decode("utf-8", errors="replace")
    for line in text.split("\n"):
        if line == "":
            if name:
                payload = "\n".join(data_lines)
                try:
                    parsed = json.loads(payload) if payload else {}
                except json.JSONDecodeError:
                    parsed = {"_raw": payload}
                out.append((name, parsed))
            name = ""
            data_lines = []
            continue
        if line.startswith("event: "):
            name = line[len("event: "):].strip()
        elif line.startswith("data: "):
            data_lines.append(line[len("data: "):])
    return out


# ---------- tests ----------


async def test_stream_delivers_events_in_order() -> None:
    runner = _make_runner(_scripted_events(final="hello world"))
    app = _build_app(runner=runner)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        token = _mint()
        resp = await client.post(
            "/v1/turns:stream",
            headers={"Authorization": f"Bearer {token}"},
            json={"session_key": "sess-1", "text": "hi"},
        )
        assert resp.status_code == 200
        events = _parse_sse(resp.content)

    names = [n for n, _ in events]
    assert names == [
        "delta",
        "tool_call",
        "tool_output",
        "delta",
        "usage",
        "done",
    ]
    assert events[0][1]["text"] == "hello "
    assert events[1][1]["name"] == "memory"
    assert events[1][1]["id"] == "tc_1"
    assert events[2][1]["output"] == "(empty)"
    assert events[3][1]["text"] == "world"
    assert events[4][1]["input_tokens"] == 12
    assert events[4][1]["output_tokens"] == 7
    done = events[5][1]
    assert done["final_text"] == "hello world"
    assert done["turn_id"].startswith("turn_")


async def test_non_streaming_returns_collected_events() -> None:
    runner = _make_runner(_scripted_events(final="bye"))
    app = _build_app(runner=runner)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        token = _mint()
        resp = await client.post(
            "/v1/turns",
            headers={"Authorization": f"Bearer {token}"},
            json={"session_key": "sess-2", "text": "hi"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["final_text"] == "bye"
        assert body["turn_id"].startswith("turn_")
        # delta + tool_call + tool_output + delta + usage + done
        assert len(body["events"]) == 6
        assert body["events"][0]["event"] == "delta"
        assert body["events"][-1]["event"] == "done"


async def test_abort_cancels_registered_task() -> None:
    """Direct unit test on the registry: abort cancels the recorded task.

    httpx's ``ASGITransport`` buffers the full response before returning,
    so a cross-request abort over the test transport isn't observable as
    a timing event. The route handler delegates to ``registry.abort``,
    which is what we exercise here. Acceptance: the task observes
    ``CancelledError`` within a second of ``registry.abort``.
    """
    import asyncio

    registry = TurnRegistry()
    handle = registry.new()
    cancelled = asyncio.Event()

    async def long_running() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    handle.task = asyncio.create_task(long_running())
    # Give the task a tick to start.
    await asyncio.sleep(0)

    aborted_in = asyncio.get_running_loop().time()
    aborted = await asyncio.wait_for(registry.abort(handle.turn_id), timeout=1.0)
    elapsed = asyncio.get_running_loop().time() - aborted_in

    assert aborted is True
    assert cancelled.is_set()
    assert elapsed < 1.0


async def test_abort_endpoint_returns_404_for_unknown_turn() -> None:
    """The HTTP surface returns NOT_FOUND when the turn id isn't tracked."""
    runner = _make_runner([])  # never used
    app = _build_app(runner=runner)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        token = _mint()
        resp = await client.post(
            "/v1/turns/turn_does_not_exist:abort",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"]["kind"] == "NOT_FOUND"
