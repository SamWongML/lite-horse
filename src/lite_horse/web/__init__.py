"""HTTP layer — FastAPI app factory + JWT auth + request-scoped context.

Imports here must NOT pull in `lite_horse.api` (the v0.3 in-process surface
is the inverse direction: webapp → api). The `lite_horse.api` purity gate
in `tests/lint/test_api_purity.py` enforces the boundary.
"""
from lite_horse.web.app import create_app

__all__ = ["create_app"]
