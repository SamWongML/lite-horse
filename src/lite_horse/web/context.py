"""Request-scoped context, propagated via ContextVar.

`auth.authenticate_request` populates this on every request that passes
the JWT gate; deps + downstream helpers read it via `get_current_context`.
The Postgres `app.user_id` GUC is set from `RequestContext.user_id` inside
the request-scoped DB session.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestContext:
    user_id: str
    external_id: str
    role: str
    request_id: str


_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "litehorse_request_context", default=None
)


def get_current_context() -> RequestContext | None:
    return _REQUEST_CONTEXT.get()


def set_current_context(ctx: RequestContext) -> Token[RequestContext | None]:
    return _REQUEST_CONTEXT.set(ctx)


def reset_current_context(token: Token[RequestContext | None]) -> None:
    _REQUEST_CONTEXT.reset(token)
