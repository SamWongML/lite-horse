"""Offline SKILL.md evolution pipeline (Phase 24).

Imported by admin tooling only. **Must not** be imported by ``lite_horse.api``
— the webapp runtime stays free of the reflection + embedding surface.
"""
from __future__ import annotations

from lite_horse.evolve.runner import EvolveResult, evolve

__all__ = ["EvolveResult", "evolve"]
