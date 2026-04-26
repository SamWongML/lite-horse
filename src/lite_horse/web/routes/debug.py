"""Local-only debug endpoints — mounted when `LITEHORSE_ENV=local`.

Used by the Phase 31 acceptance test that asserts a request's JWT is
turned into a `users` row, populates `RequestContext`, and sets
`app.user_id` on the in-flight transaction.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.storage.db import get_session_user_id
from lite_horse.web.context import RequestContext
from lite_horse.web.deps import get_db_session, get_request_context, require_admin

router = APIRouter(prefix="/v1/_debug", tags=["debug"])


@router.get("/whoami")
async def whoami(
    ctx: Annotated[RequestContext, Depends(get_request_context)],
) -> dict[str, str]:
    return {
        "user_id": ctx.user_id,
        "external_id": ctx.external_id,
        "role": ctx.role,
        "request_id": ctx.request_id,
    }


@router.get("/whoami-tx")
async def whoami_tx(
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    """Like `whoami` but also surfaces `current_setting('app.user_id')`.

    Hits the DB; covered by the round-trip integration test.
    """
    guc = await get_session_user_id(session)
    return {
        "user_id": ctx.user_id,
        "external_id": ctx.external_id,
        "role": ctx.role,
        "request_id": ctx.request_id,
        "app_user_id_guc": guc or "",
    }


@router.get("/admin-only")
async def admin_only(
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> dict[str, str]:
    return {"role": ctx.role, "user_id": ctx.user_id}
