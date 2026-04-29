"""Pricing table loader + cost math."""
from __future__ import annotations

from pathlib import Path

import pytest

from lite_horse.providers.pricing import (
    ModelPricing,
    PricingTable,
    compute_cost_usd_micro,
    get_pricing_table,
    reset_pricing_table,
)


def test_default_table_loads_known_models() -> None:
    table = reset_pricing_table()
    assert table.get("claude-sonnet-4-6") is not None
    assert table.get("claude-opus-4-7") is not None
    assert table.get("gpt-5.4") is not None


def test_cost_math_uses_input_cached_output() -> None:
    table = PricingTable(
        rows={
            "test-model": ModelPricing(
                name="test-model",
                provider="test",
                input_per_mtok=2.0,
                cached_input_per_mtok=1.0,
                output_per_mtok=4.0,
            )
        }
    )
    cost = compute_cost_usd_micro(
        model="test-model",
        input_tokens=1_000_000,
        cached_input_tokens=500_000,
        output_tokens=2_000_000,
        table=table,
    )
    # 1M*2 + 0.5M*1 + 2M*4 = 2 + 0.5 + 8 = 10.5 USD = 10_500_000 micro-USD
    assert cost == 10_500_000


def test_unknown_model_returns_zero(caplog: pytest.LogCaptureFixture) -> None:
    table = PricingTable(rows={})
    cost = compute_cost_usd_micro(
        model="not-a-model",
        input_tokens=1000,
        output_tokens=1000,
        table=table,
    )
    assert cost == 0


def test_reset_pricing_with_custom_path(tmp_path: Path) -> None:
    p = tmp_path / "pricing.yaml"
    p.write_text(
        "models:\n"
        "  - name: tiny\n"
        "    provider: test\n"
        "    input_per_mtok: 1.0\n"
        "    cached_input_per_mtok: 0.5\n"
        "    output_per_mtok: 2.0\n"
    )
    table = reset_pricing_table(p)
    assert "tiny" in table.rows
    # Reset back so subsequent tests see the real default.
    reset_pricing_table()


def test_get_pricing_table_is_cached() -> None:
    reset_pricing_table()
    a = get_pricing_table()
    b = get_pricing_table()
    assert a is b


def test_malformed_row_skipped(tmp_path: Path) -> None:
    p = tmp_path / "pricing.yaml"
    p.write_text(
        "models:\n"
        "  - name: ok\n"
        "    provider: test\n"
        "    input_per_mtok: 1.0\n"
        "    cached_input_per_mtok: 0.5\n"
        "    output_per_mtok: 2.0\n"
        "  - name: broken\n"
        "    provider: test\n"
        # missing all rates
    )
    table = reset_pricing_table(p)
    assert "ok" in table.rows
    assert "broken" not in table.rows
    reset_pricing_table()
