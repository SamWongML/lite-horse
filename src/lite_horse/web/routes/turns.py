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
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from lite_horse.agent.backends.feedback_cloud import FeedbackCloudBackend
from lite_horse.repositories.agent_repo import AgentRepo
from lite_horse.repositories.usage_repo import UsageRepo
from lite_horse.repositories.user_settings_repo import UserSettings, UserSettingsRepo
from lite_horse.storage.db import db_session
from lite_horse.storage.locks import LockTimeoutError, SessionLock
from lite_horse.storage.redis_client import Redis
from lite_horse.web.context import RequestContext
from lite_horse.web.cost_budget import check_budget, record_cost
from lite_horse.web.deps import get_redis, get_request_context
from lite_horse.web.errors import ErrorKind, http_error
from lite_horse.web.idempotency import (
    get_cached_json,
    get_cached_stream,
    store_cached_json,
    store_cached_stream,
)
from lite_horse.web.permissions import PermissionBroker
from lite_horse.web.rate_limit import check_and_consume
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
    # Phase 41: optional override; falls back to users.default_agent_id.
    agent_id: str | None = None


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


class FeedbackIn(BaseModel):
    """User-explicit outcome for one turn (Phase 44)."""

    session_key: str = Field(min_length=1, max_length=200)
    rating: Literal[-1, 0, 1]
    reason: str | None = Field(default=None, max_length=240)
    skill_slug: str | None = Field(default=None, max_length=200)
    agent_id: str | None = None


class FeedbackOut(BaseModel):
    turn_id: str
    rating: int
    source: Literal["user_explicit"]


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

    Default (production) builds a per-user agent through
    :func:`lite_horse.web.turn_engine.run_turn_streaming_for_user`,
    closing over ``mcp_pool`` / ``kms`` / ``redis`` from app state.
    Tests override this to inject a deterministic event sequence.
    """
    state = request.app.state
    runner = getattr(state, "turn_runner", None)
    if runner is not None:
        return runner  # type: ignore[no-any-return]
    mcp_pool = getattr(state, "mcp_pool", None)
    kms = getattr(state, "kms", None)
    redis = getattr(state, "redis", None)

    async def _runner(req: TurnRequest) -> AsyncIterator[Any]:
        from lite_horse.web.turn_engine import (  # noqa: PLC0415
            run_turn_streaming_for_user,
        )

        async for ev in run_turn_streaming_for_user(
            req, mcp_pool=mcp_pool, kms=kms, redis=redis
        ):
            yield ev

    return _runner


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


@dataclass
class _AgentLimits:
    """Resolved per-agent caps blended with the per-user fallbacks."""

    agent_id: str | None
    rate_limit_per_min: int | None
    cost_budget_usd_micro: int | None
    user_settings: UserSettings


async def _resolve_agent_limits(
    *, user_id: str, requested_agent_id: str | None
) -> _AgentLimits | None:
    """Resolve which agent owns this turn + the rate / cost caps.

    Per-agent overrides shadow per-user defaults. Returns ``None`` if the
    DB is unreachable (test paths that skip auth + DB plumbing).
    """
    try:
        async with db_session(user_id) as session:
            user_settings = await UserSettingsRepo(session).get()
            agent_repo = AgentRepo(session)
            if requested_agent_id is not None:
                agent_row = await agent_repo.get(requested_agent_id)
                if agent_row is None or agent_row.archived_at is not None:
                    raise http_error(
                        ErrorKind.NOT_FOUND,
                        f"agent {requested_agent_id!r} not found",
                    )
            else:
                agent_row = await agent_repo.ensure_default()
            return _AgentLimits(
                agent_id=str(agent_row.id),
                rate_limit_per_min=(
                    agent_row.rate_limit_per_min
                    if agent_row.rate_limit_per_min is not None
                    else user_settings.rate_limit_per_min
                ),
                cost_budget_usd_micro=(
                    agent_row.cost_budget_usd_micro
                    if agent_row.cost_budget_usd_micro is not None
                    else user_settings.cost_budget_usd_micro
                ),
                user_settings=user_settings,
            )
    except HTTPException:
        raise
    except Exception:
        return None


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


async def _load_user_settings(user_id: str) -> UserSettings:
    """Read per-user limits in a fresh tenant transaction.

    Same pattern as :func:`_record_usage`: a short-lived session that
    doesn't share the request-scoped one, so the read is off the SSE
    critical path.
    """
    async with db_session(user_id) as session:
        return await UserSettingsRepo(session).get()


async def _enforce_preflight_limits(
    *,
    redis: Redis | None,
    user_id: str,
    requested_agent_id: str | None,
) -> _AgentLimits | None:
    """Run rate-limit + cost-budget checks before the agent runs.

    Returns the resolved :class:`_AgentLimits` so the post-turn step can
    record cost against the same per-(user, agent) budget without
    re-querying. Returns ``None`` if no DB is reachable (test paths that
    override ``get_redis`` and skip ``app.user_id`` plumbing).
    """
    if redis is None:
        return None
    limits = await _resolve_agent_limits(
        user_id=user_id, requested_agent_id=requested_agent_id
    )
    if limits is None:
        return None
    allowed = await check_and_consume(
        redis,
        user_id=user_id,
        agent_id=limits.agent_id,
        limit_per_min=limits.rate_limit_per_min,
    )
    if not allowed:
        raise http_error(
            ErrorKind.RATE_LIMIT, "per-agent turn rate limit exceeded"
        )
    within_budget = await check_budget(
        redis,
        user_id=user_id,
        agent_id=limits.agent_id,
        budget_micro=limits.cost_budget_usd_micro,
    )
    if not within_budget:
        raise http_error(ErrorKind.FORBIDDEN, "cost_budget_exceeded")
    return limits


async def _record_cost_metered(
    *,
    redis: Redis | None,
    user_id: str,
    limits: _AgentLimits | None,
    usage: TurnUsage,
) -> None:
    if redis is None or limits is None or usage.cost_usd_micro <= 0:
        return
    await record_cost(
        redis,
        user_id=user_id,
        agent_id=limits.agent_id,
        cost_micro=usage.cost_usd_micro,
        budget_micro=limits.cost_budget_usd_micro,
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

    agent_limits = await _enforce_preflight_limits(
        redis=redis,
        user_id=ctx.user_id,
        requested_agent_id=body.agent_id,
    )

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
                agent_id=(
                    agent_limits.agent_id
                    if agent_limits is not None
                    else body.agent_id
                ),
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
    await _record_cost_metered(
        redis=redis,
        user_id=ctx.user_id,
        limits=agent_limits,
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

    agent_limits = await _enforce_preflight_limits(
        redis=redis,
        user_id=ctx.user_id,
        requested_agent_id=body.agent_id,
    )

    handle = registry.new()
    turn_req = TurnRequest(
        user_id=ctx.user_id,
        session_key=body.session_key,
        text=body.text,
        permission_mode=body.permission_mode or "auto",
        attachments=body.attachments,
        command=body.command,
        model=body.model,
        agent_id=(
            agent_limits.agent_id
            if agent_limits is not None
            else body.agent_id
        ),
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
            await _record_cost_metered(
                redis=redis,
                user_id=ctx.user_id,
                limits=agent_limits,
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


@router.post("/{turn_id}/feedback", response_model=FeedbackOut)
async def post_feedback(
    turn_id: str,
    body: FeedbackIn,
    ctx: Ctx,
) -> FeedbackOut:
    """Persist a user-explicit ``turn_outcomes`` row with ``source='user_explicit'``.

    The user supplies the same ``session_key`` they passed when starting
    the turn; we resolve the agent the same way as ``POST /v1/turns``.
    """
    agent_limits = await _resolve_agent_limits(
        user_id=ctx.user_id, requested_agent_id=body.agent_id
    )
    agent_id = agent_limits.agent_id if agent_limits is not None else body.agent_id
    if agent_id is None:
        raise http_error(
            ErrorKind.UNPROCESSABLE,
            "feedback requires a resolvable agent_id",
        )
    sink = FeedbackCloudBackend(user_id=ctx.user_id, agent_id=agent_id)
    try:
        record = await sink.record(
            session_id=body.session_key,
            turn_id=turn_id,
            source="user_explicit",
            rating=body.rating,
            reason=body.reason,
            skill_slug=body.skill_slug,
        )
    except ValueError as exc:
        raise http_error(ErrorKind.UNPROCESSABLE, str(exc)) from exc
    return FeedbackOut(turn_id=record.turn_id, rating=record.rating, source="user_explicit")


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
