# Hard contract (binding for every implementation subagent)

> Part of v0.5. Read once before the first phase of a session; re-read on conflict.

Any subagent that cannot honor one of these must **stop and escalate**.
This contract extends — does not replace — the v0.4 hard contract.
Re-read v0.4's "Hard contract" section before starting; everything
there still holds (tenancy, storage abstraction, layered config,
versioning, auth, async discipline, encryption at rest, migrations,
observability).

### Tool / hook backend abstraction (new in v0.5)

- Every tool that today reaches into `litehorse_home()` (`memory_tool`,
  `skill_manage`, `cron_manage`, `skill_view`, `skill stats`,
  `EvolutionHook._read_skill_md`, `BudgetHook._consolidate`,
  `_skills_index` in `instructions.py`) **must** route through a
  Protocol defined in `src/lite_horse/agent/backends/`.
- Two impls per backend Protocol, one for each environment:
  - `*_local.py` — wraps the existing FS code (`litehorse_home() /
    "memories"`, `skills_root()`, `litehorse_home() / "jobs.json"`).
    Used by the CLI surface and by tests.
  - `*_cloud.py` — calls into the existing repos (`MemoryRepo`,
    `SkillRepo`, `CronRepo`, `SkillProposalRepo`).
- Selection happens **once per turn** at agent factory time. Backend
  bundle is plumbed onto `RunContextWrapper.context` as a typed
  `TenantContext` dataclass.
- **No tool body may import `litehorse_home`, `MemoryStore`, `skills_root`,
  or any repo class directly.** The only allowed import is the backend
  Protocol from `agent/backends/`. CI lint asserts this.
- **No backend impl runs SQL outside `repositories/`.** The cloud
  backends are thin pass-throughs; the local backends are thin
  pass-throughs to the v0.4 FS code.

### CLI parity (new in v0.5)

- The `litehorse` CLI keeps booting against `~/.litehorse/` with **zero
  schema migrations on the user's Mac**. Migrations are cloud-only.
- Every cloud-only feature (multi-agent, vector recall, summaries,
  curator, GEPA) ships a local-mode equivalent **gated on the same code
  path**. The local impl may be slower or stub-grade (e.g. local vector
  store via `chromadb` instead of pgvector), but the user-visible
  behavior — the tools, the prompt blocks, the slash commands — is
  identical.
- Concretely: each phase's acceptance section includes a "**CLI parity
  gate**" — a manual or automated check that exercises the new
  capability end-to-end via `litehorse`, with `~/.litehorse/` as the
  only state.
- A new lint test (`tests/lint/test_cli_parity.py`) asserts that for
  every backend Protocol there is exactly one `*_local` and one
  `*_cloud` impl, both with the full method set. Drift breaks CI.

### Per-agent persona scope (new in v0.5)

- A new `agents` table owns persona, default model, permission mode,
  and enabled-tools bitmap, scoped per user. `users.default_agent_id`
  points at the user's "main" agent (auto-created on first login or
  first CLI run).
- Every tenant-scoped table that today carries `user_id` and is
  agent-shaped (`user_documents`, `skills`, `cron_jobs`,
  `skill_proposals`, `mcp_servers` user-scope, `commands` user-scope,
  `instructions` user-scope, `sessions`, `memory_chunks` from Phase 42,
  `session_summaries` from Phase 43) gains a NOT NULL `agent_id` FK.
- Migration backfills existing rows under each user's auto-created
  default agent (`slug='default'`).
- RLS policies extended: `(user_id::text = current_setting('app.user_id',
  true))` AND `(agent_id = current_setting('app.agent_id', true)::uuid
  OR current_setting('app.agent_id', true) = '')` — the empty-string
  fallback lets cross-agent admin queries (curator, GDPR delete) run
  without an agent context.
- `db_session()` accepts an optional `agent_id` and sets the
  `app.agent_id` GUC alongside `app.user_id`.

### Vector / recall (new in v0.5)

- Cloud: pgvector extension in RDS Postgres. Local CLI: `chromadb` (or
  `sqlite-vss`) on `~/.litehorse/embeddings/`. Same backend Protocol.
- Embeddings via `EmbeddingProvider` Protocol (Phase 42). Two impls:
  OpenAI `text-embedding-3-small` and Voyage `voyage-3`. Default
  driven by `LITEHORSE_EMBEDDING_PROVIDER`, BYO-key honored.
- Hybrid retrieval (BM25 + cosine) is the only retrieval shape; pure
  vector or pure FTS are not separately exposed.

### Evolution (new in v0.5)

- Curator and GEPA produce **proposals** in `skill_proposals`. They
  never auto-merge.
- Outcome classifier signals (Phase 44) feed `turn_outcomes`, which
  feeds `EvolutionHook` and the curator. Explicit thumbs-down from
  the calling website is honored same-shape as classifier output.
- Skill promotion (Phase 45) only promotes user skills with
  `unique_user_count >= 3` and `success_rate >= 0.8` and
  `use_count >= 20` (constants in `lite_horse.constants`,
  admin-tunable via env).

### What stays untouched

- Everything from v0.4's "What stays untouched" still holds.
- **Tool *names*** (`memory`, `skill_manage`, `cron_manage`,
  `skill_view`, `session_search`) and their JSON IO shapes are
  frozen. Internals can change; the wire shape cannot, because user
  prompts and bundled instructions reference the names.
- **`SKILL.md` frontmatter shape** is frozen.
- **HTTP error codes / SSE event shapes** are frozen.
- The v0.4 cron / scheduler / worker process model is unchanged
  (Phases 44–45 add new SQS message types; they do not change the
  shape of the worker).

### Library stack additions

- `pgvector >= 0.3, < 1` (Python adapter for SQLAlchemy)
- `chromadb >= 0.5, < 1` (CLI local vector store)
- `voyageai >= 0.2, < 1` (optional embedding provider)
- `tiktoken >= 0.7, < 1` (chunking)
- **No** new infra primitives. RDS gets the pgvector extension via a
  one-shot Alembic migration; everything else reuses v0.4 stack.

### Bumps

- `anthropic >= 0.65, < 1.0` (prompt caching, batch API). Phase 46.
- `openai-agents >= 0.16, < 0.18` (track current minor). Phase 46.
- `openai >= 2.5, < 3` (latest minor). Phase 46.

