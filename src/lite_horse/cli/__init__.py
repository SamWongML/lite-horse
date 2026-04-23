"""Interactive-first `litehorse` CLI (v0.3).

Do not import from this package at `lite_horse.api` import time — the CLI
surface is a separate consumer of the runtime, not part of the embeddable
API. The import-isolation test in tests/cli/test_import_isolation.py
enforces this.
"""
