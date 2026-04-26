"""BaseRepo + the `@audited` decorator stub.

Every repository takes the request-scoped `AsyncSession` and reads
`user_id` from the same `app.user_id` GUC that `db_session` sets on the
transaction. Tenant filters in queries are belt-and-braces with RLS; both
must hold.

`@audited` is a stub here — it lets repository write methods declare
*how* they should be audited (action name, target shape) without paying
the audit-log write cost in Phase 32. Phase 34's admin layer fills in
the body to write to `audit_log`. Until then the decorator is a no-op
pass-through; tests that assert audit rows live in Phase 34.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

P = ParamSpec("P")
R = TypeVar("R")


class BaseRepo:
    """Base class for every per-request repository.

    The constructor accepts the open `AsyncSession`; the GUC-derived
    `user_id` is read on demand from `current_setting('app.user_id')`
    rather than passed around so the repository can never disagree with
    the transaction it sits inside.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def current_user_id(self) -> str:
        """Return the `app.user_id` GUC; raises if unset.

        Repositories are tenant-scoped by construction; an unset GUC means
        the caller forgot to use `db_session(user_id)` and would punch a
        hole in RLS, so we fail loud.
        """
        result = await self.session.execute(
            text("SELECT current_setting('app.user_id', true)")
        )
        raw = result.scalar_one()
        if raw is None or raw == "":
            raise RuntimeError(
                "app.user_id GUC is unset — repositories must be used "
                "inside `db_session(user_id)`"
            )
        return str(raw)


def audited(
    action: str,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Stub decorator — declares the action name; Phase 34 fills the body.

    Usage:
        @audited("skill.create")
        async def create(self, ...): ...

    Phase 32 keeps it transparent so repositories already have the right
    annotations when Phase 34 lands the audit-log write.
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> R:
            return await fn(*args, **kwargs)

        wrapper.__lite_horse_audit_action__ = action  # type: ignore[attr-defined]
        return wrapper

    return decorator
