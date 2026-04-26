"""`session.execute(text(...))` is confined to a tiny allowlist.

ORM constructs (`select(...)`, `insert(...)`) are unrestricted; they're
type-checked by SQLAlchemy and don't bypass tenant scoping. Likewise
`server_default=text(...)` in column definitions is part of the DDL and
runs once at table-creation time — not a query.

What this rule guards against is *running* raw SQL against a session,
which sidesteps both the ORM type-check path and any tenant-scope
helpers. That pattern is allowed only in:

* `storage/db.py` — sets/reads the `app.user_id` GUC, plus a tiny
  readiness ping.
* `alembic/` — migrations are inherently raw DDL.
* `repositories/` — handful of FTS expressions that ORM can't model
  (lands in Phase 32+).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "lite_horse"

ALLOWED_PREFIXES = (
    SRC / "storage" / "db.py",
    SRC / "alembic",
    SRC / "repositories",
)

# Match `.execute(text(` across line breaks — catches both
# `session.execute(text("SELECT ..."))` and the formatted-on-multi-lines
# variant. ORM column defaults like `server_default=text("now()")` do not
# match because they don't sit inside an `.execute(` call.
EXECUTE_TEXT = re.compile(r"\.execute\s*\(\s*text\s*\(", re.DOTALL)


def _python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if p.is_file()]


def _is_allowed(path: Path) -> bool:
    return any(
        str(path) == str(allow) or str(path).startswith(str(allow) + "/")
        for allow in ALLOWED_PREFIXES
    )


def test_no_session_execute_text_outside_repositories() -> None:
    offenders: list[str] = []
    for path in _python_files():
        if _is_allowed(path):
            continue
        content = path.read_text(encoding="utf-8")
        if EXECUTE_TEXT.search(content):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == [], (
        "session.execute(text(...)) outside the allowlist (storage/db.py, "
        "alembic/, repositories/):\n  " + "\n  ".join(offenders)
    )
