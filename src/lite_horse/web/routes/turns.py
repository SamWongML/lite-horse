"""``/v1/turns*`` — streaming + permissions + idempotency.

The four endpoints in the locked HTTP surface:

* ``POST /v1/turns``                       — JSON, full final response
* ``POST /v1/turns:stream``                — SSE, incremental events
* ``POST /v1/turns/{turn_id}/decisions``   — resolve an ask-mode prompt
* ``POST /v1/turns/{turn_id}:abort``       — cancel an in-flight turn

Both turn-creating endpoints honour the ``Idempotency-Key`` header: a
retried request with the same key for the same user replays the
recorded response without invoking the agent again. Same-``session_key``
turns also serialise through a Redis-backed distributed lock so two
ECS tasks can't run a single user's session in parallel.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from lite_horse.api import run_turn_streaming
from lite_horse.repositories.usage_repo import UsageRepo
from lite_horse.storage.db import db_session
from lite_horse.storage.locks import LockTimeoutError, SessionLock
from lite_horse.storage.redis_client import Redis
from lite_horse.web.context import RequestContext
from lite_horse.web.deps import get_redis, get_request_context
from lite_horse.web.errors import ErrorKind, http_error
from lite_horse.web.idempotency import (
    get_cached_json,
    get_cached_stream,
    store_cached_json,
    store_cached_stream,
)
from lite_horse.web.permissions import PermissionBroker
from lite_horse.web.sse import event_done, event_error
from lite_horse.web.turns import (
    TurnRegistry,
    TurnRequest,
    TurnRunner,
    TurnUsage,
    collect_sse,
    stream_turn_to_sse,
)

router = APIRouter(prefix="/v1/turns", tags=["turns"])


# ---------- request / response models ----------


class TurnIn(BaseModel):
    session_key: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    permission_mode: Literal["auto", "ask", "ro"] | None = None
    attachments: list[dict[str, Any]] | None = None
    command: str | None = None
    model: str | None = None


class TurnOut(BaseModel):
    turn_id: str
    final_text: str
    events: list[dict[str, str]]


class DecisionIn(BaseModel):
    tool_call_id: str
    decision: Literal["allow", "deny"]


class DecisionOut(BaseModel):
    delivered: bool


class AbortOut(BaseModel):
    aborted: bool


# ---------- dependency overrides (test-injection points) ----------


def get_turn_registry(request: Request) -> TurnRegistry:
    state = request.app.state
    if not hasattr(state, "turn_registry"):
        state.turn_registry = TurnRegistry()
    reg: TurnRegistry = state.turn_registry
    return reg


def get_permission_broker(request: Request) -> PermissionBroker:
    state = request.app.state
    if not hasattr(state, "permission_broker"):
        state.permission_broker = PermissionBroker(
            redis=getattr(state, "redis", None)
        )
    broker: PermissionBroker = state.permission_broker
    return broker


def get_session_lock(request: Request) -> SessionLock | None:
    """Distributed per-session lock factory.

    Tests can override this dep to swap in :class:`MemorySessionLock` or
    skip locking entirely (return ``None``).
    """
    return getattr(request.app.state, "session_lock", None)


def get_turn_runner(request: Request) -> TurnRunner:
    """Resolve the agent stream factory.

    Default (production) wraps ``lite_horse.api.run_turn_streaming``.
    Tests override this to inject a deterministic event sequence.
    """
    state = request.app.state
    runner = getattr(state, "turn_runner", None)
    if runner is not None:
        return runner  # type: ignore[no-any-return]
    return _default_turn_runner


async def _default_turn_runner(req: TurnRequest) -> AsyncIterator[Any]:
    async for ev in run_turn_streaming(
        session_key=req.session_key,
        user_text=req.text,
        source="web",
        user_id=req.user_id,
    ):
        yield ev


# ---------- helpers ----------


async def _maybe_acquire_lock(
    lock_factory: SessionLock | None, *, key: str
) -> Any:
    if lock_factory is None:
        return _NoopLock()
    return lock_factory(key=f"turn-lock:{key}", ttl=300.0, wait=30.0)


class _NoopLock:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _session_lock_key(user_id: str, session_key: str) -> str:
    return f"{user_id}:{session_key}"


async def _record_usage(
    *, user_id: str, session_key: str, usage: TurnUsage
) -> None:
    """Persist a ``usage_events`` row in a fresh tenant transaction.

    The streaming SSE response holds the request-scoped session open until
    the generator drains, which in turn holds a connection from the pool.
    Writing usage in a *new* short-lived transaction keeps the metering
    write off that critical path.
    """
    if not usage.model or (usage.input_tokens == 0 and usage.output_tokens == 0):
        return
    async with db_session(user_id) as session:
        await UsageRepo(session).record_turn(
            session_id=session_key,
            model=usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.cached_input_tokens,
        )


# ---------- routes ----------


Ctx = Annotated[RequestContext, Depends(get_request_context)]
RedisDep = Annotated[Redis | None, Depends(get_redis)]
LockDep = Annotated[SessionLock | None, Depends(get_session_lock)]
RunnerDep = Annotated[TurnRunner, Depends(get_turn_runner)]
RegistryDep = Annotated[TurnRegistry, Depends(get_turn_registry)]
BrokerDep = Annotated[PermissionBroker, Depends(get_permission_broker)]


@router.post("", response_model=TurnOut)
async def post_turn(
    body: TurnIn,
    ctx: Ctx,
    redis: RedisDep,
    lock_factory: LockDep,
    runner: RunnerDep,
    registry: RegistryDep,
    broker: BrokerDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TurnOut:
    cached = await get_cached_json(
        redis, user_id=ctx.user_id, idem_key=idempotency_key or ""
    )
    if cached is not None:
        return TurnOut(**cached)

    lock_cm = await _maybe_acquire_lock(
        lock_factory, key=_session_lock_key(ctx.user_id, body.session_key)
    )
    try:
        async with lock_cm:
            turn_req = TurnRequest(
                user_id=ctx.user_id,
                session_key=body.session_key,
                text=body.text,
                permission_mode=body.permission_mode or "auto",
                attachments=body.attachments,
                command=body.command,
                model=body.model,
            )
            handle = registry.new()
            try:
                _raw, events = await collect_sse(
                    stream_turn_to_sse(
                        request=turn_req,
                        runner=runner,
                        broker=broker,
                        handle=handle,
                    )
                )
            finally:
                registry.remove(handle.turn_id)
    except LockTimeoutError as exc:
        raise http_error(ErrorKind.UNAVAILABLE, str(exc)) from exc

    await _record_usage(
        user_id=ctx.user_id,
        session_key=body.session_key,
        usage=handle.usage,
    )

    final_text = ""
    parsed_events: list[dict[str, str]] = []
    for name, data in events:
        parsed_events.append({"event": name, "data": data})
        if name == "done":
            try:
                final_text = json.loads(data).get("final_text", "") or ""
            except (ValueError, TypeError):
                pass
    body_out = TurnOut(
        turn_id=handle.turn_id, final_text=final_text, events=parsed_events
    )
    await store_cached_json(
        redis,
        user_id=ctx.user_id,
        idem_key=idempotency_key or "",
        body=body_out.model_dump(),
    )
    return body_out


@router.post(":stream")
async def post_turn_stream(
    body: TurnIn,
    ctx: Ctx,
    redis: RedisDep,
    lock_factory: LockDep,
    runner: RunnerDep,
    registry: RegistryDep,
    broker: BrokerDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> StreamingResponse:
    cached = await get_cached_stream(
        redis, user_id=ctx.user_id, idem_key=idempotency_key or ""
    )
    if cached is not None:
        async def _replay() -> AsyncIterator[bytes]:
            yield cached

        return StreamingResponse(_replay(), media_type="text/event-stream")

    handle = registry.new()
    turn_req = TurnRequest(
        user_id=ctx.user_id,
        session_key=body.session_key,
        text=body.text,
        permission_mode=body.permission_mode or "auto",
        attachments=body.attachments,
        command=body.command,
        model=body.model,
    )

    async def _outer() -> AsyncIterator[bytes]:
        buffer: list[bytes] = []
        lock_cm = await _maybe_acquire_lock(
            lock_factory, key=_session_lock_key(ctx.user_id, body.session_key)
        )
        try:
            async with lock_cm:
                async for chunk in stream_turn_to_sse(
                    request=turn_req,
                    runner=runner,
                    broker=broker,
                    handle=handle,
                ):
                    buffer.append(chunk)
                    yield chunk
        except LockTimeoutError as exc:
            err = event_error(kind="UNAVAILABLE", message=str(exc))
            done = event_done(turn_id=handle.turn_id, final_text="")
            buffer.extend([err, done])
            yield err
            yield done
        finally:
            registry.remove(handle.turn_id)
            await store_cached_stream(
                redis,
                user_id=ctx.user_id,
                idem_key=idempotency_key or "",
                sse_bytes=b"".join(buffer),
            )
            await _record_usage(
                user_id=ctx.user_id,
                session_key=body.session_key,
                usage=handle.usage,
            )

    headers = {
        "Cache-Control": "no-cache",
        "X-Turn-Id": handle.turn_id,
    }
    return StreamingResponse(
        _outer(), media_type="text/event-stream", headers=headers
    )


@router.post("/{turn_id}/decisions", response_model=DecisionOut)
async def post_decision(
    turn_id: str,
    body: DecisionIn,
    ctx: Ctx,
    broker: BrokerDep,
) -> DecisionOut:
    try:
        delivered = await broker.resolve(turn_id, body.tool_call_id, body.decision)
    except ValueError as exc:
        raise http_error(ErrorKind.UNPROCESSABLE, str(exc)) from exc
    return DecisionOut(delivered=delivered)


@router.post(
    "/{turn_id}:abort",
    response_model=AbortOut,
    status_code=status.HTTP_200_OK,
)
async def post_abort(
    turn_id: str,
    ctx: Ctx,
    registry: RegistryDep,
    broker: BrokerDep,
) -> AbortOut:
    aborted = await registry.abort(turn_id)
    broker.cancel_turn(turn_id)
    if not aborted:
        raise http_error(ErrorKind.NOT_FOUND, f"unknown turn {turn_id!r}")
    return AbortOut(aborted=True)
