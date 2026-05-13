# Phase 40 — Tool-backend abstraction + tenant-safe writes (+700 / +600 LOC) — BLOCKER

> Part of v0.5. See [README.md](README.md) for objective/non-goals, [_contract.md](_contract.md) for binding rules, [_architecture.md](_architecture.md) for shared types, [_briefing.md](_briefing.md) for the subagent briefing template.

**Objective.** Stop the FS-vs-DB write asymmetry. After this phase, every
agent tool that touches durable state writes to the right place for the
current request's tenant, and the CLI keeps working byte-for-byte.

**Deliverables.**

- `src/lite_horse/agent/backends/__init__.py` defines `TenantContext`:
  ```python
  @dataclass(frozen=True)
  class TenantContext:
      user_id: str | None        # None in CLI mode
      agent_id: str | None       # None in CLI mode (Phase 41 lifts this)
      memory: MemoryBackend
      skill: SkillBackend
      cron: CronBackend
      # recall + feedback added in Phases 42 / 44
  ```
- `MemoryBackend`, `SkillBackend`, `CronBackend` Protocols with these
  methods (signatures derived from current tool bodies; preserve argument
  shapes so tool wire format does not drift):
  - `MemoryBackend`: `get(kind: Literal["memory","user"]) -> str`,
    `add(kind, content) -> Usage`,
    `replace(kind, old, new) -> Usage`,
    `remove(kind, old) -> Usage`,
    `total_chars(kind) -> int`,
    `char_limit(kind) -> int`.
  - `SkillBackend`: `list() -> list[SkillSummary]`,
    `view(slug) -> SkillView`,
    `create(frontmatter, body) -> SlugVersion`,
    `patch(slug, old_string, new_string) -> SlugVersion`,
    `record_view(slug)`, `record_outcome(slug, ok, error_summary)`.
  - `CronBackend`: `list() -> list[CronJob]`,
    `create(slug, cron_expr, prompt, webhook_url=None) -> CronJob`,
    `update(slug, **patch) -> CronJob`,
    `delete(slug)`.
- `*_local.py` impls wrap the **existing** v0.4 code:
  - `MemoryLocalBackend` calls into `lite_horse.memory.store.MemoryStore`.
  - `SkillLocalBackend` calls into `lite_horse.skills.source` /
    `manage_tool` / `view_tool` / `stats`.
  - `CronLocalBackend` calls into `lite_horse.cron.jobs`.
- `*_cloud.py` impls accept an `AsyncSession` and dispatch to:
  - `MemoryCloudBackend` → `MemoryRepo`.
  - `SkillCloudBackend` → `SkillRepo` + `SkillProposalRepo`.
  - `CronCloudBackend` → `CronRepo`.
  - Each cloud impl opens **its own** short-lived transaction per call;
    it does not pin the request connection.
- `agent/factory.py::build_agent_for_user` constructs `TenantContext`
  with cloud backends, passes it through the SDK `Runner.run_streamed(...,
  context=tenant_ctx)` channel (which wires it into
  `RunContextWrapper.context`).
- `agent/factory.py::build_agent` (CLI path) constructs `TenantContext`
  with local backends and `user_id=None, agent_id=None`.
- Rewrite the five tool bodies — `memory/tool.py`, `skills/manage_tool.py`,
  `skills/view_tool.py`, `cron/manage_tool.py`, `skills/stats.py` —
  to read `ctx.context` (`TenantContext`) and call the backend.
  Tool wire shapes (function name, arguments, JSON IO) are **frozen**.
- Rewrite `BudgetHook._consolidate` (`agent/budget.py`) and
  `EvolutionHook._read_skill_md` / `_maybe_create_skill` /
  `_maybe_refine_skill` (`agent/evolution.py`) to take backends from
  the wrapped `RunContextWrapper.context`. The hook's `__init__` no
  longer needs filesystem state.
- Rewrite `_skills_index` (`agent/instructions.py:77`) to read from
  `EffectiveConfig.skills` (already resolved by `web/turn_engine.py:82`)
  in cloud mode; keep `litehorse_home() / "skills"` path under CLI.
  This requires the instructions builder to receive an effective-config
  handle — pass it through `make_instructions_for_user(eff, ...)`.
- New CI lint: `tests/lint/test_no_litehorse_home_in_tools.py` asserts
  no module under `agent/`, `memory/tool.py`, `skills/manage_tool.py`,
  `skills/view_tool.py`, `cron/manage_tool.py`, `skills/stats.py`
  imports `litehorse_home`, `MemoryStore`, `skills_root`, or any
  `*_repo` class directly. The only allowed import is from
  `agent/backends/`.
- New CI lint: `tests/lint/test_cli_parity.py` asserts each backend
  Protocol has exactly one `*_local.py` and one `*_cloud.py` impl,
  and that both expose the full method set.

**Acceptance.**

- All 470+ existing tests green; no `xfail` additions.
- New `tests/security/test_tool_tenant_isolation.py` integration test:
  spin two `db_session(user_id=A)` and `db_session(user_id=B)` agents
  back-to-back on the same task, have A's agent call `memory(action=add,
  ...)`, then have B's agent call the agent — assert B's prompt does
  **not** contain A's entry. Asserts the same for `skill_manage(create)`
  and `cron_manage(create)`.
- New `tests/cli/test_cli_byte_parity.py`: `litehorse "remember I prefer
  pnpm"` followed by `litehorse "what do I prefer?"` writes to
  `~/.litehorse/memories/MEMORY.md` exactly as v0.4 did (compare bytes
  against a golden file).
- Cross-phase gates 1–8 pass.
- `docs/PROGRESS.md` v0.5 row 40 flipped to ✅.

