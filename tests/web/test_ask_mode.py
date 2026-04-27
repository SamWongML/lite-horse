"""Ask-mode permission round-trip.

The runner exposes the broker via the ``TurnRequest`` (in production it
would be an agent hook); here a fake runner publishes a permission
request, awaits the decision, and then yields the corresponding tool
output. The streaming generator emits the ``permission_request`` SSE
event; a separate decisions request resolves the future; the runner
proceeds. We exercise both paths (allow + deny).
"""
from __future__ import annotations

import asyncio
import base64
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
    get_permission_broker,
    get_session_lock,
    get_turn_runner,
)
from lite_horse.web.turns import TurnRegistry, TurnRequest

pytestmark = pytest.mark.asyncio

_SECRET = b"phase35-ask-test-secret-do-not-use-in-prod"
_KID = "phase35-ask-kid"
_AUDIENCE = "lite-horse"
_ISSUER = "http://localhost:9999"


@dataclass
class _FakeRunResult:
    final_output: str
    input_tokens: int = 0
    output_tokens: int = 0


class StreamDelta:
    def __init__(self, text: str) -> None:
        self.text = text


class StreamToolCall:
    def __init__(self, name: str, arguments: str, id: str) -> None:
        self.name = name
        self.arguments = arguments
        self.id = id


class StreamToolOutput:
    def __init__(self, name: str, output: str, id: str) -> None:
        self.name = name
        self.output = output
        self.id = id


class StreamDone:
    def __init__(self, result: _FakeRunResult) -> None:
        self.result = result


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


def _mint(sub: str = "ask-user") -> str:
    return jwt.encode(
        {
            "sub": sub,
            "role": "user",
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


def _build_app(*, runner, broker: PermissionBroker) -> Any:
    app = create_app()
    app.dependency_overrides[get_turn_runner] = lambda: runner
    app.dependency_overrides[get_session_lock] = lambda: None
    app.dependency_overrides[get_permission_broker] = lambda: broker
    app.state.turn_registry = TurnRegistry()
    app.state.permission_broker = broker
    return app


def _parse_sse(raw: bytes) -> list[tuple[str, dict[str, Any]]]:
    import json

    out: list[tuple[str, dict[str, Any]]] = []
    name = ""
    data_lines: list[str] = []
    for line in raw.decode().split("\n"):
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


def _decision_runner(broker: PermissionBroker, *, expected_decision_event: asyncio.Event):
    """Runner that asks for a decision in mid-stream.

    The runner blocks on ``broker.request_decision`` after announcing a
    tool call; once resolved it emits the corresponding tool output and
    a final delta + done. The ``expected_decision_event`` lets the test
    verify ordering.
    """

    async def runner(req: TurnRequest) -> AsyncIterator[Any]:
        # Resolve which turn we're driving via the registry's active id.
        # In the test we read the X-Turn-Id header instead and post
        # decisions against that, so just look up the only in-flight
        # turn from app state.
        turn_id = _current_turn_id_holder.value
        yield StreamDelta(text="thinking ")
        yield StreamToolCall(name="memory", arguments="{}", id="tc_xyz")
        decision = await broker.request_decision(
            turn_id, "tc_xyz", tool="memory", args={}
        )
        expected_decision_event.set()
        if decision == "allow":
            yield StreamToolOutput(name="memory", output="ok", id="tc_xyz")
            yield StreamDelta(text="done.")
            yield StreamDone(result=_FakeRunResult(final_output="thinking done."))
        else:
            yield StreamDelta(text="(blocked)")
            yield StreamDone(result=_FakeRunResult(final_output="thinking (blocked)"))

    return runner


# Trivial holder so the runner can fish out the turn_id without changing
# the runner signature. The route allocates the id and writes here
# before kicking off the stream.
class _TurnIdHolder:
    value: str = ""


_current_turn_id_holder = _TurnIdHolder()


async def _drive_ask_mode(*, decision: str) -> tuple[int, list[tuple[str, dict[str, Any]]]]:
    broker = PermissionBroker(redis=None)
    decision_seen = asyncio.Event()
    runner = _decision_runner(broker, expected_decision_event=decision_seen)
    app = _build_app(runner=runner, broker=broker)

    # Record the turn_id when the route allocates it. We patch the
    # registry's ``new`` to publish into the holder.
    registry: TurnRegistry = app.state.turn_registry
    original_new = registry.new

    def capture_new() -> Any:
        handle = original_new()
        _current_turn_id_holder.value = handle.turn_id
        return handle

    registry.new = capture_new  # type: ignore[method-assign]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        token = _mint()

        async def _do_stream() -> bytes:
            resp = await client.post(
                "/v1/turns:stream",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "session_key": "ask-sess",
                    "text": "do something",
                    "permission_mode": "ask",
                },
            )
            return resp.content

        async def _do_decision() -> int:
            # Wait for the runner to register a permission request
            # before posting the decision. Poll the broker — the
            # request is enqueued on ``broker.request_decision``.
            for _ in range(200):
                state = broker._turns.get(_current_turn_id_holder.value)
                if state is not None and state.requests.get("tc_xyz") is not None:
                    break
                await asyncio.sleep(0.01)
            resp = await client.post(
                f"/v1/turns/{_current_turn_id_holder.value}/decisions",
                headers={"Authorization": f"Bearer {token}"},
                json={"tool_call_id": "tc_xyz", "decision": decision},
            )
            return resp.status_code

        stream_task = asyncio.create_task(_do_stream())
        decision_status = await _do_decision()
        body = await stream_task

    return decision_status, _parse_sse(body)


async def test_ask_mode_allow_unblocks_tool_run() -> None:
    decision_status, events = await _drive_ask_mode(decision="allow")
    assert decision_status == 200
    names = [n for n, _ in events]
    # Order: delta, tool_call, permission_request, tool_output, delta, usage, done
    assert "permission_request" in names
    pr_idx = names.index("permission_request")
    assert names[pr_idx + 1] == "tool_output"
    assert names[-1] == "done"
    done_data = events[-1][1]
    assert done_data["final_text"] == "thinking done."


async def test_ask_mode_deny_blocks_tool_run() -> None:
    decision_status, events = await _drive_ask_mode(decision="deny")
    assert decision_status == 200
    names = [n for n, _ in events]
    assert "permission_request" in names
    # No tool_output after deny.
    assert "tool_output" not in names
    done_data = events[-1][1]
    assert done_data["final_text"] == "thinking (blocked)"


async def test_decisions_endpoint_rejects_invalid_decision() -> None:
    broker = PermissionBroker(redis=None)

    async def noop(_req: TurnRequest) -> AsyncIterator[Any]:
        yield StreamDone(result=_FakeRunResult(final_output=""))

    app = _build_app(runner=noop, broker=broker)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        token = _mint()
        resp = await client.post(
            "/v1/turns/turn_anything/decisions",
            headers={"Authorization": f"Bearer {token}"},
            json={"tool_call_id": "tc_x", "decision": "maybe"},
        )
    assert resp.status_code == 422
