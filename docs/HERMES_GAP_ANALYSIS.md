# Hermes-Agent Gap Analysis

> Reference: [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
> Target: lite-horse v0.4 → AWS ECS deployment as a multi-tenant personal-assistant
> engine for an "agent management center" website (Claude-managed-agents-style).
>
> Date: 2026-05-07
> Branch: `claude/hermes-agent-research-azy54`

This document maps the reference Hermes agent's architecture against the
current lite-horse repo, surfaces blocking gaps for the stated multi-tenant /
self-evolving deployment goal, and proposes a prioritized list of missing
puzzles.

---

## 1. What Hermes is reputed for (the bar to clear)

| Feature | Where it lives in the reference repo |
|---|---|
| **Built-in learning loop** — autonomous skill creation after multi-step tasks, plus a background **Curator** that ages, consolidates, and archives skills | `agent/curator.py`, `~/.hermes/skills/.usage.json` |
| **Layered persistent memory** — `SOUL.md`, `MEMORY.md`, `USER.md`, plus session FTS5 over SQLite, plus 8 pluggable external memory providers (mem0, supermemory, byterover, hindsight, holographic, openviking, retaindb, honcho) | `agent/memory_provider.py`, `agent/memory_manager.py` |
| **Profile-based isolation** — fully isolated `HERMES_HOME` per persona; 119+ files resolve through `get_hermes_home()` | `hermes_cli/main.py::_apply_profile_override` |
| **Multi-platform gateway** — single agent core, ~18 messaging adapters | `gateway/platforms/*` |
| **Seven execution backends** — Docker (hardened), SSH, Modal/Daytona/Vercel Sandbox with serverless hibernation | `terminal/*` |
| **Model-agnostic routing** (200+ providers) | `providers/`, `plugins/model-providers/` |
| **First-class plugin architecture** — drop Python files into `~/.hermes/plugins/` | `plugins/` |
| **Companion `hermes-agent-self-evolution`** repo — DSPy + GEPA optimizes skills, prompts, tool descriptions, and even agent code via PR proposals | external repo |

The agent is text-first: skills and memory live as Markdown that humans read,
edit, and git-commit. Vector retrieval is delegated to opt-in external
providers — there's no native hybrid retriever.

---

## 2. What lite-horse already does well

These are real strengths over the reference and shouldn't be regressed:

- **Cloud-native data plane**: Postgres (SQLAlchemy 2.0 + Alembic), Redis,
  S3 (4 buckets, SSE-KMS, Glacier lifecycle on audit-archive). Hermes is
  local-files-only.
- **Real multi-tenant data model on paper**: every table carries `user_id`;
  `app.user_id` GUC + RLS pattern in `MemoryRepo`; KMS-encrypted BYO API keys
  per user per provider; per-user `cost_budget` and `rate_limit` in Redis;
  per-session distributed lock.
- **Layered config precedence** — `bundled` → `official` → `user` for
  skills/instructions/commands/cron/MCP, with admin push and per-user opt-out
  rows (`user_official_opt_out`). Hermes has nothing equivalent for
  multi-tenant operator content.
- **Streaming HTTP API** with `Idempotency-Key` replay (24h S3 cache),
  ask-mode permission round-trip, and abort.
- **Scheduler + worker split** — APScheduler 60 s tick + standalone SQS
  worker for turns, HMAC-SHA256-signed webhooks, daily evolve queue.
- **First-class observability** — structlog JSON, OTel (FastAPI / SQLAlchemy /
  httpx auto-instrumentation, OTLP → X-Ray), CloudWatch EMF metrics,
  dashboards + alarms via CDK. Hermes has only TUI logs.
- **Unified `ModelProvider`** abstraction over OpenAI + Anthropic with a
  pricing table for micro-USD cost metering.
- **Production deploy artifacts** — single-file CDK stack provisioning VPC,
  ECS, RDS Multi-AZ, ElastiCache, SQS, S3, KMS, Secrets Manager, ADOT
  sidecars; `ci.yml` + `deploy.yml` (OIDC → ECR → ECS).

The deployment story is materially ahead of Hermes for the stated use case.

---

## 3. CRITICAL BUG — agent tools break multi-tenancy in the cloud path

> **Severity: blocker for ECS deployment serving multiple users.**

The cloud read path is per-user, but every **write** path the agent itself
exercises is filesystem-backed and global.

### 3.1 Read/write asymmetry

`web/turn_engine.py::run_turn_streaming_for_user` (lines 81-134) correctly
reads `memory.md` / `user.md` from the per-user `MemoryRepo`
(`user_documents` table) and renders them into the system prompt.

But the agent's `memory` tool (`memory/tool.py:31`) does:

```python
store = MemoryStore.for_memory() if target == "memory" else MemoryStore.for_user()
```

…where `MemoryStore.for_memory()` (`memory/store.py:43`) hardcodes:

```python
p = litehorse_home() / "memories" / "MEMORY.md"
```

**Consequence**: when User A's agent calls `memory(action='add', ...)` on
ECS task X, it writes to a single `MEMORY.md` file on that container's
filesystem. User B hitting the same task next sees A's "private" memory
folded into their system prompt. Worse, User A on task Y won't see their
own write. Read injection comes from Postgres; tool writes go to a shared
local file. The two stores diverge instantly under any real traffic.

### 3.2 Same bug, additional surfaces

| Tool / hook | File / line | FS path used | Should write to |
|---|---|---|---|
| `memory_tool` | `memory/tool.py:31` | `litehorse_home()/memories/MEMORY.md` | `MemoryRepo` (per `user_id`) |
| `BudgetHook._consolidate` | `agent/budget.py:158` | same | same |
| `skill_manage` (create/patch) | `skills/manage_tool.py:30` | `skills_root() = litehorse_home()/skills/` | `SkillRepo` (per `user_id`) |
| `EvolutionHook._read_skill_md` | `agent/evolution.py:242` | same | same |
| `cron_manage` | `cron/jobs.py:45` | `litehorse_home()/jobs.json` | `CronRepo` (per `user_id`) |
| Skill activation in prompt | `agent/instructions.py:77` | `litehorse_home()/skills/` | `EffectiveConfig.skills` (already exists, just not wired in) |
| Skill stats (`skill_view` + outcome recording) | `skills/stats.py:56`, `skills/activation.py:192` | `litehorse_home()/...` | `SkillRepo` + per-user counters |

`agent/instructions.py::make_instructions()` (used by the local CLI path)
also reads `MemoryStore.for_memory().render_block()` from disk, but the
cloud path goes through `make_instructions_for_user()` with injected text,
so prompt assembly is fine — only writes are broken.

### 3.3 Why this passed local tests

`docker-compose.yml` runs a single `api` container, so the FS is unified
within one process. The bug only manifests on multi-task ECS, on container
restart (state loss), or when scheduler/worker tasks operate on a different
container's state than the api task that wrote it.

### 3.4 Fix (required before any production deploy)

Replace the FS-backed primitives with thin DB-backed equivalents that
accept `user_id` (already plumbed into `RunContextWrapper` via
`turn_engine.run_turn_streaming_for_user`):

1. Promote `user_id` into `RunContextWrapper.context` (or a dedicated
   `TenantContext`) at agent build time.
2. Rewrite `memory_tool`, `skill_manage`, `cron_manage` to read `user_id`
   from `ctx` and dispatch to `MemoryRepo` / `SkillRepo` / `CronRepo`.
3. Rewrite `BudgetHook._consolidate` and `EvolutionHook._read_skill_md` /
   `_maybe_create_skill` / `_maybe_refine_skill` to take a repo handle
   injected at hook construction (`LiteHorseHooks(...)` already runs
   inside the per-user request scope).
4. Rewire `make_instructions` (CLI path) to dual-mode — keep FS for
   `litehorse` REPL, use repo for cloud.
5. Wire skill activation (`_skills_index` in `instructions.py:77`) to read
   from `EffectiveConfig.skills` (already resolved upstream and available
   on `eff`).
6. Delete `MemoryStore` and `cron/jobs.py` filesystem code paths once the
   migration is done — they're dual-use today and the dual-path is the
   defect.

This is the single largest deliverable on the list. Without it, the rest
of the multi-tenancy work is moot.

---

## 4. Evolution gaps vs. Hermes

lite-horse has the right shape (distiller-on-success / refiner-on-failure
hooks + offline evolve worker) but stops well short of the reference.

### 4.1 No Curator equivalent

Hermes' `agent/curator.py` is a background process that periodically:

- transitions skills `active → stale → archived` based on `last_activity_at`
- spawns an auxiliary-model review proposing **consolidations** (merge
  redundant skills) and **drift patches**
- only touches `created_by: 'agent'` skills (bundled stay frozen)
- exposes a user-facing `pin` to lock skills

lite-horse has a `skill_proposals` table but no scheduled curator pass that
*generates* proposals from usage stats. `skills/stats.py` records outcomes
but nothing acts on them at the corpus level.

**Missing puzzle**: a curator job (already-have `cron_jobs` infra is the
right place) that runs daily per user, reads `skill.usage_count`,
`success_count`, `error_count`, age, and emits consolidation /
archival / patch proposals. Auto-archive after N days unused.

### 4.2 Offline evolve worker is single-skill, single-pass

`evolve/runner.py` proposes one revision for one skill from recorded
failures. The Hermes self-evolution sister repo runs **GEPA** (Genetic-Pareto
Prompt Evolution) with:

- read defs → generate eval set from execution traces
- GEPA produces variants
- evaluate each on the eval set
- constraint gates (tests, size, benchmarks)
- emit a PR for human review

The cost is reportedly $2-10/run, no GPU. Adding this to the daily evolve
queue would close the most distinctive Hermes feature gap.

**Missing puzzle**: an eval-set generator that mines execution traces
(SQS → S3 archive of failed turns), a fitness function that doesn't only
count "did the next turn pass," and population-level optimization rather
than single-shot.

### 4.3 No skill-promotion / cross-user knowledge sharing

A skill that User A discovered which would help every user never gets
seen by anyone else. The `bundled → official → user` precedence has the
slot for it (the "official" tier), but no mechanism promotes a
high-quality user skill into "official" candidates.

**Missing puzzle**: an admin queue that surfaces frequently-successful
user skills as candidates for `official` promotion (with ToS / privacy
review). Could be as simple as a daily aggregator that ranks user skills
by `unique_user_count × success_rate × use_count`.

### 4.4 No reflection on conversation outcome

Hermes' Curator and the in-loop skill creation both react to *trajectories*.
lite-horse's `EvolutionHook` only fires distillation when `tool_call_count
>= 5`, and only fires refinement when an explicit error marker matches a
regex (`"success": false`, `traceback`, etc.). Many real failures are
silent — model says "I tried but couldn't" without an error marker.

**Missing puzzle**: an outcome classifier that's separate from regex
matching — either a small LLM grader call at end-of-turn, or a thumbs-up /
thumbs-down feedback API that a calling website can post per turn.

### 4.5 No tool / prompt evolution, only skill evolution

Hermes' GEPA loop optimizes **tool descriptions** and **system prompts**
in addition to skills. lite-horse evolves only `SKILL.md` content. The
bundled instructions in `bundled/instructions/` are static.

**Missing puzzle**: extend the offline evolve target set to include
instruction blocks (per user, layered) and tool description strings.

### 4.6 No fitness eval set

Both repos lack a built-in eval harness in the agent core. Hermes punts
this to the sister repo. lite-horse has nothing. Without an eval set,
none of the evolution proposals can be scored at population level.

**Missing puzzle**: per-skill golden evals (a small set of `(input,
expected_outcome)` cases per skill, stored in `skill_proposals` or a new
`skill_evals` table). Required for any non-trivial GEPA-style loop.

---

## 5. Memory architecture gaps

### 5.1 No semantic / vector retrieval

Memory is flat Markdown injected wholesale into the system prompt, capped
at 2400 chars (`memory.md`) + 1500 chars (`user.md`). No embeddings, no
similarity search, no hybrid retriever.

For a personal-assistant engine that should recall "what did we discuss
about my Q3 strategy three months ago?" this is too tight by ~3 orders of
magnitude. Hermes solves this by deferring to optional providers (mem0 /
supermemory / etc.); lite-horse doesn't even have an interface to plug
one in.

**Missing puzzle**: pgvector extension in RDS + a `memory_chunks` table
keyed on `user_id` with `embedding vector(1536)`, plus a `memory_search`
tool added to the always-on bundle. Embeddings can be Voyage / OpenAI /
Anthropic-Voyage. Cost is small at the embedding layer; the win is large.

Note: Hermes also doesn't have a *native* hybrid retriever — this is a
place lite-horse could be **better than Hermes** with modest effort.

### 5.2 No episodic / session-summary memory

`session_search` does Postgres `tsvector` FTS over messages — keyword
match only. There's no per-session "what happened" summary that survives
into future sessions.

**Missing puzzle**: at session-end (or on idle 24h), generate a 1-3
sentence session summary, store it in a `session_summaries` table. Inject
the most-recent N summaries (or vector-retrieved relevant ones) into the
system prompt. This is the standard ChatGPT-style "memory" pattern.

### 5.3 No `SOUL.md` / persona-as-text

Hermes' `SOUL.md` is the persona/identity layer. lite-horse only has
`memory.md` (agent notes) + `user.md` (about the human). For an "agent
management center" where users may want to spin up *different agents*
with different personas (a shopping assistant vs. a coding buddy), this
slot is missing.

**Missing puzzle**: a per-user-per-agent `agents` table with `persona`
text, default model, default tool bundle, and a `default_agent_id` on
`users`. Today the system has one agent shape per user. This is also
how you map onto the "Claude managed agents" mental model the brief
references.

### 5.4 No memory consolidation across sessions

`Consolidator` runs at WARNING budget tier within a single turn. There's
no equivalent that runs across sessions to compact older `memory.md`
entries when the file approaches its char cap. Once `memory.md` hits 2400
chars, `MemoryFull` raises and writes silently fail.

**Missing puzzle**: a daily per-user job that runs a compaction agent
when memory utilization > 80%, merging similar entries and dropping
stale ones. Same `cron_jobs` infra.

### 5.5 No shared / org memory

If two users at the same company want to share project conventions
("we use pnpm, not npm"), there's no org/team layer. Probably out of
scope for v1, but worth noting as the natural next axis.

---

## 6. Multi-tenancy gaps (beyond the §3 bug)

### 6.1 RLS is on, but only on four tables

**Calibrated 2026-05-07.** RLS is real:
`alembic/versions/20260426_0001_initial_schema.py:535` enables RLS and
creates `tenant_isolation` policies on `messages`, `sessions`,
`user_documents`, `skill_proposals`;
`20260430_0002_phase39_user_limits.py:43` adds `FORCE ROW LEVEL
SECURITY` so even table-owners cannot bypass. `db_session()` sets the
`app.user_id` GUC on connection acquire.

The remaining gap is **scope**: `skills`, `cron_jobs`, `commands`,
`instructions`, `mcp_servers`, `usage_events`, and `audit_log` are
WHERE-clause-guarded only. A single missed `where(user_id=...)` on any
of those is a data leak.

**Missing puzzle**: extend RLS to cover every tenant-scoped table.
v0.5 Phase 41 covers this for new tables it adds (`agents`,
`memory_chunks`, `session_summaries`, `turn_outcomes`); a back-fill
migration for the older user-scoped tables is a Tier-2 item.

### 6.2 No per-user MCP server isolation at network layer

`McpPool.acquire(user_id, eff)` returns user-scoped MCP servers, but
they're outbound HTTP — a misconfigured MCP endpoint owned by User A
could be hit by User B if the pool key is wrong. Verify pool keying
includes `user_id` in the cache key, not just the URL.

### 6.3 No per-user PII purge / GDPR delete

There's an `audit_log` and an `opt_out` table, but no documented
"delete everything for user X" operation. For a website hosting personal
assistants, this is required for EU users.

**Missing puzzle**: an admin endpoint + worker job that drops all
`user_id`-scoped rows + S3 prefixes + KMS-encrypted secrets in one
transaction (with audit-archive copy retained).

### 6.4 No per-tenant model usage caps separate from cost

Per-user daily *cost* budget exists. Per-user daily *token* budget,
*request* budget, and *concurrent-turn* budget do not. Cost-only caps
are gameable (a single 1M-token call on a cheap model passes). For a
managed-agents platform, you want layered caps.

### 6.5 No tenant tier model

All users are equal in the schema. A real product distinguishes free /
pro / enterprise with different caps, models, MCP allowances, and rate
limits. The `users` table has `role` (admin/user) but no `plan_tier`.

---

## 7. Agent-loop and tool-use gaps

### 7.1 No explicit planner

Both Hermes and lite-horse rely on the SDK's native function-calling +
tool descriptions; neither has an explicit ReAct/Plan-and-Execute step.
Hermes compensates with rich procedural memory (skills); lite-horse has
the same mechanism but a smaller skill library.

For complex multi-step tasks ("plan a vacation, book the flights, add
to calendar"), an explicit planner pass that decomposes the user
request before tool selection would help. Optional.

### 7.2 No subagent / handoff pattern

The OpenAI Agents SDK supports agent handoffs natively (one agent
delegating to a specialist agent). lite-horse uses side-agents
(`Consolidator`, `EvolutionHook`'s distiller/refiner) but never user-
facing handoffs.

For an "agent management center" where the user might say "let me talk
to the coding agent now," handoff is the right abstraction. Missing
today.

### 7.3 Tool surface is small

Always-on tools: `memory`, `session_search`, `skill_manage`, `skill_view`,
`cron_manage`. Conditional: `WebSearchTool`, MCP, GitHub. Missing for
"personal assistant":

- file/document attachment ingestion (read PDFs / images the user
  uploads via the website)
- email send/draft (with user-scoped OAuth)
- calendar read/write (Google / Microsoft)
- code execution sandbox (Hermes has 7 backends; lite-horse has none)

The S3 `LITEHORSE_S3_BUCKET_ATTACHMENTS` bucket is provisioned but the
ingestion tool isn't built.

### 7.4 No streaming-tool-output to the user

SSE streams `StreamDelta` (text), `StreamToolCall`, `StreamToolOutput`.
Tool outputs reach the user as the agent finishes them, but tools that
themselves stream (web search progress, code-exec stdout) don't get
forward-streamed. Probably fine for v1.

---

## 8. Operational / deploy gaps

These are things the CDK stack handles for itself but the application
doesn't yet exercise:

- **Idempotent task definition rollouts**: `deploy.yml` updates the ECS
  service with a new image tag. No blue/green or canary; a bad deploy
  takes the whole tenant base down. CodeDeploy ECS or AppMesh canary
  shapes would help.
- **Migration safety**: `alembic upgrade` runs in CI but not in the deploy
  pipeline. Adding a one-shot "migrate" ECS task that runs before the
  service rollout is standard.
- **Backup / restore drill**: RDS automated backups are on by default in
  CDK; no documented restore runbook. `SECRET_ROTATION.md` exists but
  no `RESTORE.md`.
- **Per-tenant data export**: the `LITEHORSE_S3_BUCKET_EXPORTS` bucket is
  provisioned, the endpoint isn't.
- **Load tests**: `tests/load/` directory exists but appears placeholder.
  No documented capacity model (turns/sec/task, p99 latency, cost/turn).
- **Cold-start of ECS Fargate**: turn p99 includes container cold-start
  the first time scale-out happens. Provisioned concurrency or always-on
  baseline tasks would smooth this. Hermes' Modal/Daytona backends are
  the equivalent for the in-process tool-execution sandbox.

---

## 9. Surface gaps for "agent management center" use case

The brief specifically mentions a Claude-managed-agents-style website. To
serve that surface, lite-horse needs:

- **Multiple agents per user** (today: one). See §5.3.
- **Agent CRUD endpoints** — `POST /v1/agents`, `GET /v1/agents/{id}`,
  `DELETE /v1/agents/{id}`. The `default_agent_id` slot on the user.
- **Per-agent tool / MCP / skill bundles** — each agent picks from the
  user's pool. The `EffectiveConfig` resolver needs an `agent_id` axis.
- **Public sharing / publishing of agents** (optional but expected).
- **Conversation history grouped by agent** (today: by `session_key`,
  but an agent should own its sessions).
- **Run lifecycle UI** — abort/resume/replay are partially there
  (`POST /v1/turns/{turn_id}:abort`); replay isn't.
- **WebSocket alternative to SSE** for browser UIs that need
  bidirectional cancel signals. SSE works but the abort path goes via
  a separate POST and races.

---

## 10. Smaller but worth-noting

- **No prompt caching strategy beyond `prompt_cache_retention="24h"`**.
  Anthropic prompt caching is content-addressed; for the same user
  hitting the same agent across turns, the cache can be much hotter if
  the system prompt is segmented (static skills index → per-session
  memory → per-turn input) so only the tail invalidates.
- **`anthropic >= 0.30, < 1.0`** in `pyproject.toml` is far behind the
  current SDK (sub-1.0 versions are 2024-vintage). Anthropic prompt
  caching, batch API, and computer-use require newer SDK + Sonnet 4.6 /
  Opus 4.7 model IDs that aren't pinned anywhere.
- **`openai-agents >= 0.14.1, < 0.15`** is similarly tight; the SDK has
  shipped multiple breaking-change minors since.
- **No model-id constants** — `gpt-5.4`, `claude-opus`, `claude-sonnet`
  are scattered as strings. Hermes routes through provider profiles for
  this.
- **`api.py` is "deprecated for cloud, kept for dev REPL"** but
  `web/turn_engine.py` still imports private internals from it
  (`_ensure_ready`, `_process_stream_event`, `_StreamCounters`). That
  back-channel needs cleanup before `api.py` can actually be removed.
- **No `requirements.txt` for `infra/`** committed (only
  `infra/requirements.txt` for CDK-app deps). The Lambda-runtime case
  doesn't apply here; this is fine.
- **No HSTS / CSP headers on FastAPI**; ALB will terminate TLS but app-
  level security headers are a defense-in-depth gap.
- **Audit log is append-only in DB but archives to S3 — verify the
  shipper exists**. The bucket is provisioned with versioning + Glacier
  lifecycle; I didn't find a writer path that uploads `audit_log` rows
  to the bucket.
- **Documents folder mentions "v0.4 multi-tenant cloud rollout" in
  PROGRESS.md** — read that for the in-flight phase plan; the
  conclusions here are independent of any phase still in progress.

---

## 11. Prioritized punch list

Top-down, rough effort estimates:

### Tier 1 — required before any production deploy

1. **§3** Fix the FS-vs-DB write asymmetry. Migrate `memory_tool`,
   `skill_manage`, `cron_manage`, `BudgetHook._consolidate`, and
   `EvolutionHook` skill IO to per-user repos. **(Blocker.)**
2. **§5.3 + §9** Introduce `agents` table with persona, default model,
   tool bundle. Add agent CRUD. Pivot session ownership by agent.
3. **§6.1** Extend RLS coverage to `skills`, `cron_jobs`, `commands`,
   `instructions`, `mcp_servers`, `usage_events`, `audit_log` (RLS
   already on for `messages` / `sessions` / `user_documents` /
   `skill_proposals`).

> Tracked in [plans/v0.5-tenant-evolve-recall.md](plans/v0.5-tenant-evolve-recall.md)
> Phases 40 (#1) and 41 (#2 + new-table RLS for #3); legacy-table RLS
> back-fill is a Tier-2 item.

### Tier 2 — closes the Hermes-feature gap

4. **§5.1** pgvector + `memory_chunks` + `memory_search` tool. Most
   leverage per dollar of effort.
5. **§4.1** Curator background job — daily per-user pass over skill
   stats producing consolidation/archive/patch proposals.
6. **§5.2** Per-session summaries with vector retrieval into prompt.
7. **§4.4** Outcome classifier — small LLM grader at end-of-turn or a
   feedback API the website calls.

### Tier 3 — differentiation and polish

8. **§4.2** Population-level (GEPA-style) offline evolve loop with a
   real fitness eval set (§4.6).
9. **§4.3** User-skill → official-skill promotion queue.
10. **§7.3** Attachment ingestion, calendar tools, email tools.
11. **§5.4** Cross-session memory compaction job.
12. **§9** WebSocket alternative; replay endpoint; per-agent session
    grouping in UI.
13. **§6.4 / §6.5** Multi-axis quotas and tiered plans.

### Tier 4 — operational hygiene

14. **§8** Migration step in deploy pipeline; restore runbook; canary
    rollouts; load tests + capacity model.
15. **§6.3** GDPR delete pipeline.
16. **§10** SDK pin bumps; model-id constants; HSTS/CSP middleware;
    audit-archive shipper verification.

---

## 12. One-line summary

lite-horse has a stronger **deployment substrate** than Hermes (Postgres,
RDS, ECS, OTel, KMS, RLS-on-paper) and a **smaller, cleaner agent core**,
but the agent's tool implementations still write to the local filesystem
— so the multi-tenant design exists at the data plane only, not at the
agent's behavior plane. Fix that, then close the evolution-loop and
vector-memory gaps, and the result is competitive with Hermes for the
website-personal-assistant use case while being meaningfully more
production-ready.
