"""Pricing table loaded from ``data/pricing.yaml``.

The table maps a model name to a per-million-token rate for input,
cached input, and output. Cost computations use micro-USD (10⁻⁶ USD)
integers so we can persist into ``usage_events.cost_usd_micro`` as a
``BIGINT`` without floating-point drift.

Loaded once at module import; tests can call :func:`reset_pricing_table`
to swap in a fresh path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPricing:
    """Pricing row for one model. Rates are USD per 1M tokens."""

    name: str
    provider: str
    input_per_mtok: float
    cached_input_per_mtok: float
    output_per_mtok: float


@dataclass(frozen=True)
class PricingTable:
    """A frozen lookup over model name → :class:`ModelPricing`."""

    rows: dict[str, ModelPricing]

    def get(self, model: str) -> ModelPricing | None:
        return self.rows.get(model)


_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "pricing.yaml"
_TABLE: PricingTable | None = None


def _load(path: Path) -> PricingTable:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows: dict[str, ModelPricing] = {}
    for entry in raw.get("models", []):
        try:
            row = ModelPricing(
                name=str(entry["name"]),
                provider=str(entry["provider"]),
                input_per_mtok=float(entry["input_per_mtok"]),
                cached_input_per_mtok=float(
                    entry.get("cached_input_per_mtok", entry["input_per_mtok"])
                ),
                output_per_mtok=float(entry["output_per_mtok"]),
            )
        except (KeyError, TypeError, ValueError):
            _log.warning("pricing.yaml: skipping malformed row %r", entry)
            continue
        rows[row.name] = row
    return PricingTable(rows=rows)


def get_pricing_table() -> PricingTable:
    """Return the process-wide pricing table, loading on first call."""
    global _TABLE  # noqa: PLW0603
    if _TABLE is None:
        _TABLE = _load(_DEFAULT_PATH)
    return _TABLE


def reset_pricing_table(path: Path | None = None) -> PricingTable:
    """Reload the pricing table from ``path`` (or the default). Tests-only."""
    global _TABLE  # noqa: PLW0603
    _TABLE = _load(path or _DEFAULT_PATH)
    return _TABLE


def compute_cost_usd_micro(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    table: PricingTable | None = None,
) -> int:
    """Return the cost of one turn in micro-USD (10⁻⁶ USD).

    ``input_tokens`` is the *uncached* input count; if you have a separate
    cached count, pass ``cached_input_tokens`` and we'll bill the
    cached-input rate on those. We don't double-charge: callers must pass
    in the non-cached portion as ``input_tokens``.

    Unknown models log at WARN and return 0 so a missing pricing row
    never breaks a turn.
    """
    pricing = (table or get_pricing_table()).get(model)
    if pricing is None:
        _log.warning("pricing.yaml: no row for model %r — recording cost=0", model)
        return 0
    # cost(USD) = tokens / 1e6 * rate ; cost(μUSD) = tokens * rate
    cost_micro = (
        input_tokens * pricing.input_per_mtok
        + cached_input_tokens * pricing.cached_input_per_mtok
        + output_tokens * pricing.output_per_mtok
    )
    return round(cost_micro)
