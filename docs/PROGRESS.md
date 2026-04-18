# hermes-lite — implementation progress

Tracks execution of [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md). Check each
box as the acceptance criteria for that phase are met.

## Phase 0 — scaffold
- [x] `pyproject.toml` with `openai-agents>=0.14.1,<0.15` pin
- [x] Source tree (`src/hermes_lite/{agent,memory,sessions,skills,gateway/platforms,cron,tools}`)
- [x] `constants.py` with char limits, thresholds, schema version
- [x] `config.py` YAML + `.env` loader
- [x] `cli.py` click group stub (`chat`, `gateway`, `cron`)
- [x] `tests/conftest.py` with isolated `HERMESLITE_HOME` fixture
- [x] `uv run hermeslite --help` prints subcommands
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
- [ ] `memory/store.py` — `MemoryStore` with `add` / `replace` / `remove`
- [ ] Char-limit enforcement (`MemoryFull`) and duplicate guard
- [ ] Injection-pattern / invisible-Unicode validators (`UnsafeMemoryContent`)
- [ ] `render_block()` Hermes-format header + `§` delimiters
- [ ] `memory/tool.py` — `memory` `@function_tool`
- [ ] `tests/test_memory_store.py` green

## Phase 3 — Skills
- [ ] `skills/source.py` — `make_skills_capability()` wrapping `LocalDirLazySkillSource`
- [ ] `skills/manage_tool.py` — `skill_manage` with create/patch/edit/delete/write_file/remove_file/list
- [ ] Slug validation + path-traversal guard
- [ ] Bundled `skills/plan/SKILL.md` and `skills/skill-creator/SKILL.md`
- [ ] First-run sync into `~/.hermeslite/skills/`
- [ ] `tests/test_skill_manage_tool.py` green

## Phase 4 — Autonomous skill creation hook
- [ ] `agent/evolution.py` — `EvolutionHook` counting tool calls, side-agent distiller
- [ ] Cost/error guards (`max_turns=4`, swallow exceptions)
- [ ] `tests/test_evolution_hook.py` green

## Phase 5 — Iteration budget pressure
- [ ] `agent/budget.py` — `BudgetHook` with caution/warning tiers
- [ ] Tier change injected once per threshold into tool-result stream
- [ ] `tests/test_budget_hook.py` green

## Phase 6 — Dynamic instructions
- [ ] `agent/instructions.py` — SOUL → time → MEMORY → USER → SKILLS → AGENTS.md → tool guidance
- [ ] Frozen-snapshot reads at session start
- [ ] `tests/test_instructions_assembly.py` green

## Phase 7 — Agent factory
- [ ] `agent/factory.py` — `build_agent()` assembling model, tools, hooks
- [ ] `HermesLiteHooks` composite wrapping `BudgetHook` + `EvolutionHook`
- [ ] `bind_db()` wired at CLI/gateway/cron startup

## Phase 8 — CLI chat
- [ ] `cli.py` chat REPL with SDK `Runner.run`
- [ ] Session persistence across restarts verified
- [ ] `memory(...)` and `session_search(...)` callable end-to-end

## Phase 9 — Telegram gateway
- [ ] `gateway/session_key.py` — `build_session_key()`
- [ ] `gateway/guard.py` — per-session lock + interrupt queue
- [ ] `gateway/platforms/telegram.py` — allowlist-guarded adapter
- [ ] `gateway/runner.py` — dispatch + signal-driven shutdown
- [ ] `gateway.pid` written/removed on start/stop
- [ ] DM round-trip works

## Phase 10 — APScheduler cron
- [ ] `cron/jobs.py` — `JobStore` (jobs.json)
- [ ] `cron/scheduler.py` — AsyncIOScheduler + crontab/aliases + delivery
- [ ] Log + Telegram delivery handlers
- [ ] `@hourly`/`@daily` job fires and delivers

## Phase 11 — Built-in tools
- [ ] `WebSearchTool()` wired (opt-in)
- [ ] MCP server attach example documented

## Phase 12 — Telemetry (optional v1)
- [ ] Default SDK tracing left on (no custom processor in v1)

## Hardening
- [ ] `ruff check src tests` clean
- [ ] `mypy src` (strict) clean
- [ ] `pytest -q` clean (unit + e2e)
- [ ] `README.md` with install + run instructions
- [ ] systemd unit files (`gateway.service`, `cron.service`)
- [ ] `cloc src/hermes_lite/` under 4,000 lines
