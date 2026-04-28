"""``usage_events`` writer — one row per turn for the metering pipeline.

Reads the tenant ``user_id`` from the GUC the same way every other repo
does. The cost calculation lives in :mod:`lite_horse.providers.pricing`;
this repo's job is the persistence side.

Aggregations (by-day-by-model) are issued directly out of the admin
route via the ORM ``func.sum`` shape — no read API on this class is
needed yet.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import insert

from lite_horse.models.usage_event import UsageEvent
from lite_horse.providers.pricing import compute_cost_usd_micro
from lite_horse.repositories.base import BaseRepo


class UsageRepo(BaseRepo):
    async def record_turn(
        self,
        *,
        session_id: str | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> int:
        """Insert a row, returning the computed cost in micro-USD.

        ``input_tokens`` is the *uncached* count: the cached portion is
        billed separately at the cached-input rate.
        """
        cost_micro = compute_cost_usd_micro(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )
        user_id = UUID(await self.current_user_id())
        await self.session.execute(
            insert(UsageEvent).values(
                user_id=user_id,
                session_id=session_id,
                model=model,
                input_tokens=int(input_tokens),
                output_tokens=int(output_tokens),
                cached_input_tokens=int(cached_input_tokens),
                cost_usd_micro=cost_micro,
            )
        )
        return cost_micro
