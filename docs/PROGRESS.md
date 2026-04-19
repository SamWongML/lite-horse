# lite-horse — implementation progress

Tracks execution of [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md). Check each
box as the acceptance criteria for that phase are met.

## Phase 0 — scaffold
- [x] `pyproject.toml` with `openai-agents>=0.14.1,<0.15` pin
- [x] Source tree (`src/lite_horse/{agent,memory,sessions,skills,gateway/platforms,cron,tools}`)
- [x] `constants.py` with char limits, thresholds, schema version
- [x] `config.py` YAML + `.env` loader
- [x] `cli.py` click group stub (`chat`, `gateway`, `cron`)
- [x] `tests/conftest.py` with isolated `LITEHORSE_HOME` fixture
- [x] `uv run litehorse --help` prints subcommands
- [x] `uv run pytest -q`, `ruff check`, `mypy src` all clean

## Phase 1 — SQLite + FTS5 session store
- [x] `sessions/db.py` — `SessionDB` with WAL, `BEGIN IMMEDIATE` retry+jitter, checkpoints
- [x] FTS5 virtual table + insert/delete/update triggers
- [x] `_sanitize_fts5_query` (unmatched quotes, hyphenated terms, trailing booleans)
- [x] `search_messages` with `source_filter` / `exclude_sources` / `role_filter`
- [x] `sessions/sdk_session.py` — Agents SDK `Session` protocol adapter
- [x] `sessions/search_tool.py` — `session_search` `@function_tool` + `bind_db()`
- [x] `tests/test_session_db_fts5.py` — 7 tests green

## Phase 2 — Memory layer (MEMORY.md + USER.md)
- [x] `memory/store.py` — `MemoryStore` with `add` / `replace` / `remove`
- [x] Char-limit enforcement (`MemoryFull`) and duplicate guard
- [x] Injection-pattern / invisible-Unicode validators (`UnsafeMemoryContent`)
- [x] `render_block()` header + `§` delimiters
- [x] `memory/tool.py` — `memory` `@function_tool`
- [x] `tests/test_memory_store.py` green

## Phase 3 — Skills
- [x] `skills/source.py` — `make_skills_capability()` wrapping `LocalDirLazySkillSource`
- [x] `skills/manage_tool.py` — `skill_manage` with create/patch/edit/delete/write_file/remove_file/list
- [x] Slug validation + path-traversal guard
- [x] Bundled `skills/plan/SKILL.md` and `skills/skill-creator/SKILL.md`
- [x] First-run sync into `~/.litehorse/skills/`
- [x] `tests/test_skill_manage_tool.py` green

## Phase 4 — Autonomous skill creation hook
- [x] `agent/evolution.py` — `EvolutionHook` counting tool calls, side-agent distiller
- [x] Cost/error guards (`max_turns=4`, swallow exceptions)
- [x] `tests/test_evolution_hook.py` green

## Phase 5 — Iteration budget pressure
- [x] `agent/budget.py` — `BudgetHook` with caution/warning tiers
- [x] Tier change injected once per threshold into tool-result stream
- [x] `tests/test_budget_hook.py` green

## Phase 6 — Dynamic instructions
- [x] `agent/instructions.py` — SOUL → time → MEMORY → USER → SKILLS → AGENTS.md → tool guidance
- [x] Frozen-snapshot reads at session start
- [x] `tests/test_instructions_assembly.py` green

## Phase 7 — Agent factory
- [x] `agent/factory.py` — `build_agent()` assembling model, tools, hooks
- [x] `LiteHorseHooks` composite wrapping `BudgetHook` + `EvolutionHook`
- [x] `bind_db()` wired at CLI/gateway/cron startup

## Phase 8 — CLI chat
- [x] `cli.py` chat REPL with SDK `Runner.run` (factored `_repl_loop` for testability)
- [x] `--session-id` flag resumes an existing session; fresh `cli-<uuid>` otherwise
- [x] `db.end_session` called on REPL exit (`/exit`, `/quit`, `:q`, EOF, ^C)
- [x] Session persistence across restarts verified (`tests/e2e/test_chat_roundtrip.py`)
- [x] `memory(...)` and `session_search(...)` callable end-to-end
- [x] Fixed latent `SessionDB._init_schema` bug (executescript auto-commit vs. `_writer()`)

## Phase 9 — Telegram gateway
- [x] `gateway/session_key.py` — `build_session_key()`
- [x] `gateway/guard.py` — per-session lock + interrupt queue
- [x] `gateway/platforms/telegram.py` — allowlist-guarded adapter
- [x] `gateway/runner.py` — dispatch + signal-driven shutdown (`make_handler` factored for tests)
- [x] `gateway.pid` written/removed on start/stop
- [x] `litehorse gateway` CLI wired to `run_gateway`
- [x] Guardrails: disabled config / missing token / empty allowlist all `SystemExit`
- [x] Tests green — `test_gateway_session_key.py`, `test_gateway_guard.py`, `test_gateway_runner.py`
- [ ] DM round-trip works (requires live `TELEGRAM_BOT_TOKEN`; verify manually)

## Phase 10 — APScheduler cron
- [x] `cron/jobs.py` — `JobStore` (jobs.json) with atomic tmp+rename writes
- [x] `cron/scheduler.py` — `AsyncIOScheduler` + alias/crontab parser + signal-driven shutdown
- [x] Log + Telegram delivery handlers (`DELIVERY_HANDLERS` dispatch)
- [x] `cron.pid` written/removed on start/stop
- [x] `litehorse cron` CLI wired to `run_scheduler_blocking`
- [x] Tests green — `test_cron_jobs.py`, `test_cron_scheduler.py`
- [ ] `@hourly`/`@daily` job fires and delivers end-to-end (requires live run; verify manually)

## Phase 11 — Built-in tools
- [x] `WebSearchTool()` wired (opt-in via `tools.web_search` in `config.yaml`)
- [x] MCP server attach example documented (README — `MCPServerStreamableHttp` recipe)

## Phase 12 — Telemetry (optional v1)
- [x] Default SDK tracing left on (no custom processor in v1)

## Hardening
- [x] `ruff check src tests` clean
- [x] `mypy src` (strict) clean
- [x] `pytest -q` clean (unit + e2e) — 105 passed
- [x] `README.md` with install + run instructions (chat / gateway / cron + systemd)
- [x] systemd unit files (`deploy/gateway.service`, `deploy/cron.service`)
- [x] `cloc src/lite_horse/` under 4,000 lines — 2,115 lines of Python
