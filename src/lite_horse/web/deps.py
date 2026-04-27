"""FastAPI dependencies: request context, DB session, admin gate.

Every protected route declares `Depends(get_request_context)` (cheap;
re-uses the already-authenticated context for the request) or
`Depends(get_db_session)` (which itself depends on the context, opening a
tenant-scoped transaction with the `app.user_id` GUC pre-set).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.storage import make_kms
from lite_horse.storage.db import db_session
from lite_horse.storage.kms import Kms
from lite_horse.storage.redis_client import Redis
from lite_horse.web.auth import authenticate_request
from lite_horse.web.context import (
    RequestContext,
    reset_current_context,
    set_current_context,
)
from lite_horse.web.errors import ErrorKind, http_error


async def get_request_context(request: Request) -> AsyncIterator[RequestContext]:
    ctx = await authenticate_request(request)
    token = set_current_context(ctx)
    try:
        yield ctx
    finally:
        reset_current_context(token)


async def get_db_session(
    ctx: Annotated[RequestContext, Depends(get_request_context)],
) -> AsyncIterator[AsyncSession]:
    async with db_session(ctx.user_id) as session:
        yield session


async def require_admin(
    ctx: Annotated[RequestContext, Depends(get_request_context)],
) -> RequestContext:
    if ctx.role != "admin":
        raise http_error(ErrorKind.FORBIDDEN, "admin role required")
    return ctx


def get_redis(request: Request) -> Redis | None:
    """Return the app-scoped Redis client, or ``None`` if disabled.

    The app lifespan attaches the client to ``app.state.redis``. Tests can
    override this dep to swap in a fake or short-circuit caching.
    """
    return getattr(request.app.state, "redis", None)


def get_kms(request: Request) -> Kms:
    """Return the app-scoped Kms instance, materialising on first call.

    Stored on ``app.state.kms`` so the AWS Encryption SDK's data-key cache
    is shared across requests in the same process.
    """
    state = request.app.state
    if not hasattr(state, "kms"):
        state.kms = make_kms()
    kms: Kms = state.kms
    return kms
