"""``UsageRepo.record_turn`` writes a usage_events row + computes cost."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.repositories.usage_repo import UsageRepo

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_record_turn_inserts_row_with_cost(
    pg_session: AsyncSession,
) -> None:
    repo = UsageRepo(pg_session)
    cost = await repo.record_turn(
        session_id="sess-1",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cached_input_tokens=100,
    )
    # 1000 * 3.00 + 100 * 0.30 + 500 * 15.00 = 3000 + 30 + 7500 = 10530
    assert cost == 10_530

    row = (
        await pg_session.execute(
            text(
                "SELECT model, input_tokens, output_tokens, "
                "cached_input_tokens, cost_usd_micro, session_id "
                "FROM usage_events ORDER BY id DESC LIMIT 1"
            )
        )
    ).one()
    assert row.model == "claude-sonnet-4-6"
    assert row.input_tokens == 1000
    assert row.output_tokens == 500
    assert row.cached_input_tokens == 100
    assert row.cost_usd_micro == 10_530
    assert row.session_id == "sess-1"


async def test_record_turn_unknown_model_zero_cost(
    pg_session: AsyncSession,
) -> None:
    repo = UsageRepo(pg_session)
    cost = await repo.record_turn(
        session_id=None,
        model="not-a-model",
        input_tokens=10,
        output_tokens=20,
    )
    assert cost == 0

    row = (
        await pg_session.execute(
            text(
                "SELECT cost_usd_micro FROM usage_events ORDER BY id DESC LIMIT 1"
            )
        )
    ).one()
    assert row.cost_usd_micro == 0
