"""``/v1/users/me:*`` — account-level lifecycle endpoints (Phase 46).

Two surfaces:

* ``POST /v1/users/me:request-delete`` — schedule a GDPR delete. Inserts
  a :class:`GdprDeleteRequest` row with ``scheduled_at = now() + 7d`` so
  the user keeps a one-week grace window to recover. A 409 fires if a
  pending request already exists — the partial unique index in the
  migration owns the invariant; the explicit check above it is a polite
  fast-path so the client sees ``CONFLICT`` instead of a 5xx.

* ``DELETE /v1/users/me:cancel-delete`` — cancel the pending request.
  Returns 404 if the user has nothing pending; the cancel is a no-op
  in that case.

Both routes resolve ``user_id`` from the authenticated request context
(:func:`get_request_context`); the body never carries it.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.models.gdpr_delete_request import GdprDeleteRequest
from lite_horse.repositories.gdpr_delete_repo import GdprDeleteRepo
from lite_horse.web.context import RequestContext
from lite_horse.web.deps import get_db_session, get_request_context
from lite_horse.web.errors import ErrorKind, http_error
from lite_horse.web.schemas import GdprDeleteRequestOut

router = APIRouter(prefix="/v1/users/me", tags=["user-lifecycle"])

GDPR_DELETE_GRACE = timedelta(days=7)


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
Ctx = Annotated[RequestContext, Depends(get_request_context)]


def _to_out(row: GdprDeleteRequest) -> GdprDeleteRequestOut:
    return GdprDeleteRequestOut(
        requested_at=row.requested_at,
        scheduled_at=row.scheduled_at,
        cancelled_at=row.cancelled_at,
        completed_at=row.completed_at,
    )


@router.post(
    ":request-delete",
    response_model=GdprDeleteRequestOut,
    status_code=status.HTTP_201_CREATED,
)
async def request_delete(
    ctx: Ctx, session: DbSession
) -> GdprDeleteRequestOut:
    repo = GdprDeleteRepo(session)
    existing = await repo.get_pending_for_user(ctx.user_id)
    if existing is not None:
        raise http_error(
            ErrorKind.CONFLICT,
            "a delete request is already pending; cancel it first",
        )
    scheduled_at = datetime.now(UTC) + GDPR_DELETE_GRACE
    try:
        row = await repo.create(user_id=ctx.user_id, scheduled_at=scheduled_at)
    except IntegrityError as exc:
        raise http_error(
            ErrorKind.CONFLICT,
            "a delete request is already pending; cancel it first",
        ) from exc
    return _to_out(row)


@router.delete(
    ":cancel-delete", response_model=GdprDeleteRequestOut
)
async def cancel_delete(
    ctx: Ctx, session: DbSession
) -> GdprDeleteRequestOut:
    row = await GdprDeleteRepo(session).cancel_pending(ctx.user_id)
    if row is None:
        raise http_error(
            ErrorKind.NOT_FOUND, "no pending delete request to cancel"
        )
    return _to_out(row)


__all__ = ["router"]
