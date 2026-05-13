# Phase 41 — Per-agent personas + agent CRUD (+800 / +600 LOC)

> Part of v0.5. See [README.md](README.md) for objective/non-goals, [_contract.md](_contract.md) for binding rules, [_architecture.md](_architecture.md) for shared types, [_briefing.md](_briefing.md) for the subagent briefing template.

**Objective.** Replace the implicit "one agent per user" model with an
explicit `agents` table. Existing per-user state migrates onto each
user's auto-created default agent, and the CLI gains `litehorse agent`
subcommands so a Mac user can run `litehorse --agent coder` next to
`litehorse --agent shopper` against the same `~/.litehorse/`.

**Deliverables.**

- Alembic `0004_phase41_agents.py`: creates `agents` table, adds
  `default_agent_id` to `users`, adds nullable `agent_id` to
  `user_documents`, `skills` (user-scope), `cron_jobs` (user-scope),
  `sessions`, `skill_proposals`, `mcp_servers` (user-scope only),
  `commands` (user-scope only), `instructions` (user-scope only).
  Backfill: one default agent per user (`slug='default'`,
  `name='default'`, `is_default=true`); attach existing rows to it.
  After backfill, `ALTER COLUMN agent_id SET NOT NULL` on user-scope
  rows; official-scope rows keep `agent_id NULL`.
- RLS policy update on every affected table: existing
  `(user_id::text = current_setting('app.user_id', true))` becomes
  `... AND (current_setting('app.agent_id', true) = '' OR
  agent_id::text = current_setting('app.agent_id', true))`.
  The empty-string fallback preserves admin-context queries.
- `storage/db.py::db_session(user_id, agent_id=None)` sets both GUCs.
- `models/agent.py`, `repositories/agent_repo.py`.
- `web/routes/agents.py` with the seven endpoints in the surface delta.
- `TurnRequest` gains optional `agent_id`. Resolution order:
  request body → `users.default_agent_id` → 422 if neither.
- `web/turn_engine.py::run_turn_streaming_for_user` opens
  `db_session(user_id, agent_id)`, resolves per-agent (not just
  per-user) memory + effective config + permission mode + default model
  + cost / rate caps.
- `EffectiveConfig` resolver gains an `agent_id` axis: user-scope
  skills/MCP/commands/instructions are filtered by `agent_id` (or a
  global "available to all my agents" flag — for v0.5 we keep it
  strict: a user-scope row belongs to exactly one agent).
- CLI:
  - `litehorse agent ls` lists agents in `~/.litehorse/agents/`.
  - `litehorse agent create <slug> --persona "..."`.
  - `litehorse agent use <slug>` sets the default persisted in
    `~/.litehorse/config.yaml`.
  - `litehorse --agent <slug> "..."` overrides for a single command.
  - Local layout becomes `~/.litehorse/agents/<slug>/{memory.md,
    user.md, skills/, jobs.json}`. v0.4-style flat layout migrated on
    first run by symlinking `default` to the legacy paths so manual
    edits don't break.
- Per-agent rate limit + cost budget: when `agents.rate_limit_per_min`
  / `agents.cost_budget_usd_micro` are set, they shadow the user-level
  limits from Phase 39. Redis keys become
  `rate:turn:{user_id}:{agent_id}:{epoch_min}` and
  `cost:day:{user_id}:{agent_id}:{YYYYMMDD}`.

**Acceptance.**

- Migration runs on a snapshot of staging in <60 s; backfill correctness
  verified by `tests/migrations/test_agent_backfill.py`.
- A user can create agents `coder` and `shopper`, each with a different
  persona, and writes to `coder`'s memory do not appear in `shopper`'s
  prompt.
- `tests/web/test_agents_api.py` covers the seven endpoints.
- `tests/security/test_agent_isolation.py`: an integration leak test
  proves a query for user A's agent X returns 0 rows from agent Y under
  RLS with both GUCs set.
- **CLI parity gate.** `litehorse agent create coder --persona ...`,
  `litehorse --agent coder "remember I use rust"`,
  `litehorse --agent coder "what language?"` returns "rust";
  `litehorse --agent shopper "what language?"` does **not** return
  "rust". All state under `~/.litehorse/agents/`.
- Cross-phase gates 1–8 pass.
- `docs/PROGRESS.md` v0.5 row 41 flipped to ✅.

