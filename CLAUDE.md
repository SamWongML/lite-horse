# CLAUDE.md — lite-horse

Python 3.11+, `src/`-layout, managed by **uv**. Behavioral merge: surface tradeoffs, write the minimum, change only what the task requires, verify before declaring done.

## Commands — use `uv run *` (pre-allowlisted in `.claude/settings.local.json`)

Dev tools (pytest/ruff/mypy) live in `[project.optional-dependencies] dev`, so they need `--extra dev`. Runtime tools (`litehorse`, `alembic`) don't.

| Task                 | Command                                                       |
|----------------------|---------------------------------------------------------------|
| All tests            | `uv run --extra dev pytest`                                   |
| One test             | `uv run --extra dev pytest tests/path/test_x.py::test_name`   |
| Lint                 | `uv run --extra dev ruff check src tests`                     |
| Format               | `uv run --extra dev ruff format src tests`                    |
| Type-check           | `uv run --extra dev mypy src`                                 |
| CLI                  | `uv run litehorse <subcommand>`                               |
| Add dep / dev dep    | `uv add <pkg>` / `uv add --optional dev <pkg>`                |
| Sync (with dev)      | `uv sync --extra dev`                                         |
| Alembic revision     | `uv run alembic revision -m "msg"`                            |
| Alembic upgrade      | `uv run alembic upgrade head`                                 |

## NEVER

- **Never** call `.venv/bin/python`, `.venv/bin/pytest`, or bare `python -m <tool>`. Always `uv run <tool>` — it resolves the venv + lockfile + `PYTHONPATH` and skips the permission prompt every other invocation triggers.
- **Never** `pip install` or hand-edit `[project] dependencies` in `pyproject.toml`. Use `uv add` / `uv remove` so the lockfile stays in sync.
- **Never** `source .venv/bin/activate`. Each `uv run` invocation is self-contained.
- **Never** bypass hooks (`--no-verify`, `--no-gpg-sign`) or skip failing tests. Fix the root cause.

## Repo map

- `src/lite_horse/` — runtime. Subpackages: `agent/`, `cli/`, `web/` (FastAPI), `worker/` (SQS), `scheduler/` (APScheduler), `repositories/`, `models/`, `storage/`, `providers/`, `sessions/`.
- `tests/` mirrors `src/`. `asyncio_mode = auto` is set in `pyproject.toml`; just decorate with `@pytest.mark.asyncio` where the file isn't already module-async.
- `docs/PROGRESS.md` — phase ledger; `docs/plans/v0.X-*.md` — active plan with acceptance gates.
- Alembic migrations: `src/lite_horse/alembic/versions/NNNN_phaseN_*.py` (chain by `down_revision`).

## Project workflow

- Work is phase-scoped. The current plan in `docs/plans/` defines the success criteria — read it before implementing.
- Each phase ends by flipping its row in `docs/PROGRESS.md` from ☐ to ✅ with a one-paragraph shipped-summary in the same prose style as adjacent phases.
- **Hard parity rule:** every cloud feature ships a `*_local` backend so the `litehorse` CLI keeps working against `~/.litehorse/`. Cloud tools must not import `litehorse_home` — `tests/lint/test_no_litehorse_home_in_tools.py` enforces this.

## Behavioral rules

**Think before coding.** State assumptions explicitly. If multiple interpretations exist, surface them; don't pick silently. Push back when a simpler approach exists. If something is unclear, stop and ask.

**Simplicity.** Minimum code that solves the stated problem. No speculative abstractions, no flexibility that wasn't asked for, no error handling for impossible cases, no comments that restate what the code already says.

**Surgical changes.** Touch only what the task requires — every changed line should trace to the user's request. Don't refactor adjacent code, fix unrelated formatting, or delete pre-existing dead code (mention it instead). Remove the imports/vars your changes orphan; leave pre-existing orphans alone unless asked.

**Goal-driven.** Convert vague tasks into verifiable goals before coding: write a failing test, then make it pass; or define an explicit `uv run …` check. Loop until the check passes — don't claim done without running it.
