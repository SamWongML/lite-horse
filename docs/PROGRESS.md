# lite-horse — phase status

**Active plan:** [plans/v0.5-tenant-evolve-recall.md](plans/v0.5-tenant-evolve-recall.md)

One row per phase. Flip ☐ → ✅ only when every acceptance checkbox in the
plan file for that phase is green. Do not put plan detail here — it belongs
in the plan file.

---

## v0.1 — Hermes port on OpenAI Agents SDK — ✅ SHIPPED (2026-04-19)

2,115 LOC runtime, 105 tests green. Detail: [plans/v0.1-hermes-port.md](plans/v0.1-hermes-port.md).

| # | Subject | Status |
|---|---|---|
| 0  | scaffold                           | ✅ |
| 1  | SQLite + FTS5 session store        | ✅ |
| 2  | memory layer (MEMORY.md + USER.md) | ✅ |
| 3  | skills                             | ✅ |
| 4  | autonomous skill creation hook     | ✅ |
| 5  | iteration-budget pressure          | ✅ |
| 6  | dynamic instructions               | ✅ |
| 7  | agent factory                      | ✅ |
| 8  | CLI chat                           | ✅ (dropped in v0.2 P15) |
| 9  | Telegram gateway                   | ✅ (dropped in v0.2 P15) |
| 10 | APScheduler cron                   | ✅ |
| 11 | built-in tools                     | ✅ |
| 12 | telemetry                          | ✅ |
|    | hardening                          | ✅ |

## v0.2 — embed in webapp + strengthen self-evolution — ✅ SHIPPED (2026-04-23)

~2,720 runtime LOC + ~450 evolve LOC, 245 tests green. Detail:
[plans/v0.2-embed-and-evolve.md](plans/v0.2-embed-and-evolve.md).
Webapp-side acceptance (round-trip + proposal merge UI) is verified
out-of-band in the PM webapp repo.


| # | Subject | Status |
|---|---|---|
| 13 | prompt-cache fix + skill-write injection defense   | ✅ |
| 14 | `skill_view` tool + kill dead capability           | ✅ |
| 15 | drops: gateway, CLI REPL, systemd, Telegram dep    | ✅ |
| 16 | `lite_horse.api` webapp surface                    | ✅ |
| 17 | cron webhook delivery + `cron_manage` tool         | ✅ |
| 18 | compression-as-consolidation                       | ✅ |
| 19 | periodic memory nudge                              | ✅ |
| 20 | skill stats sidecar + in-use refinement            | ✅ |
| 21 | conditional skill activation                       | ✅ |
| 22 | structured error classifier                        | ✅ |
| 23 | `config.mcp_servers` + `token_count` cleanup       | ✅ |
| 24 | offline `lite_horse.evolve` pipeline               | ✅ |
| 25 | final hardening & docs                             | ✅ |

## v0.3 — interactive-first `litehorse` CLI — ✅ SHIPPED (2026-04-25)

476 tests green. Detail:
[plans/v0.3-cli-entrypoint.md](plans/v0.3-cli-entrypoint.md).
Reverses v0.2's no-CLI stance. Bare `litehorse` drops into a persistent
REPL with streaming markdown, slash commands, tool-call approval,
session resume, and cost meter — same class of interaction as the
webapp. Scripted subcommand tree (`sessions`, `skills`, `cron`, …) is
the secondary surface. Stack: prompt_toolkit + rich + click-default-group
+ Typer. `litehorse-debug` deleted in Phase 30.

| # | Subject | Status |
|---|---|---|
| 26 | CLI foundation + scripted skeleton (doctor/version/config)       | ✅ |
| 27 | Interactive REPL core (streaming, toolbar, Ctrl-C, /help/exit)   | ✅ |
| 28 | Slash commands + session mgmt + tool approval + attachments     | ✅ |
| 29 | Scripted subcommand parity (sessions/skills/cron/memory/logs)    | ✅ |
| 30 | Structured logs, `/logs`, `debug share`, delete litehorse-debug  | ✅ |

## v0.4 — cloud multi-tenant service — ✅ SHIPPED (2026-04-30)

Detail: [plans/v0.4-cloud-multi-tenant.md](plans/v0.4-cloud-multi-tenant.md).
Re-platforms the in-process Python library into a horizontally-scalable
cloud service: Postgres + RLS for sessions/skills/memory/cron, layered
config (official → org → user), admin/audit surface, streaming HTTP API
with idempotency + permission-prompt round-trip, scheduler/worker split,
KMS-encrypted BYO provider keys, cost meter, observability, and IaC.
Targets v0.2 webapp surface as the public contract; CLI from v0.3 stays
as a thin client. Predecessor: v0.3.

| #  | Subject | Status |
|----|---|---|
| 31 | Foundations: storage layer + ORM + Alembic + FastAPI skeleton    | ✅ |
| 32 | SessionDB port to Postgres                                       | ✅ |
| 33 | Layered config: user-scope CRUD + effective-config resolver      | ✅ |
| 34 | Admin layer: official-scope CRUD, versioning, audit, cache inval | ✅ |
| 35 | Streaming + permissions + idempotency                            | ✅ |
| 36 | Scheduler + worker services, org-wide cron                       | ✅ |
| 37 | Multi-provider, KMS-encrypted BYO keys, cost meter, GitHub tools | ✅ |
| 38 | Observability, IaC, deploy pipeline                              | ✅ |
| 39 | Hardening: RLS, secret rotation, MCP pool, evolve, load + leak   | ✅ |

### Blocked / in progress
Phase 31 shipped 2026-04-26 in three atomic commits (31a infra +
storage protocols + local impls; 31b ORM models + Alembic initial
migration + RLS; 31c FastAPI skeleton + JWT/JWKS auth + cloud
storage impls + CI boundary lint). Phase 32 shipped 2026-04-26:
v0.3 `SessionDB` replaced by tenant-scoped async Postgres
`SessionRepo` / `MessageRepo` (with tsvector FTS) for the cloud
path, plus a sync `LocalSessionRepo` for the dev REPL / single-user
CLI. Phase 33 shipped 2026-04-27 in three atomic commits (33a
repos + bundled config; 33b effective-config resolver + agent
rewire; 33c HTTP route surface + Redis cache). Phase 34 shipped
2026-04-27: ``/v1/admin/*`` CRUD with versioning + rollback for
every official entity, audit-log writes on every admin action,
mandatory-enforced opt-out gating (422), and Redis pub/sub
``effective-config-invalidate`` so admin writes evict caches across
ECS tasks. Phase 35 shipped 2026-04-27: SSE streaming + non-streaming
JSON for ``/v1/turns*``, ``Idempotency-Key`` 24 h Redis cache (replays
both JSON bodies and raw SSE bytes), ask-mode permission round-trip
via ``PermissionBroker`` (in-process futures with Redis pub/sub
fallback for cross-task delivery), per-session distributed lock, and
abort endpoint backed by ``TurnRegistry``. Phase 36 shipped 2026-04-28:
``MessageQueue`` storage protocol with SQS (aioboto3) + in-memory impls;
standalone ``scheduler`` service running an APScheduler 60 s tick that
scans ``cron_jobs`` cross-tenant, expands official-scope jobs to one
``CronMessage`` per active user, and stamps ``last_fired_at`` for
idempotency; standalone ``worker`` service long-polling SQS,
dispatching turns and signing webhook deliveries; ``cron/delivery``
HMAC key now resolves through ``SecretsProvider`` in cloud envs with
a 5-min TTL cache; Dockerfile + docker-compose updates so api /
scheduler / worker share one image. Phase 37 shipped 2026-04-28:
``ModelProvider`` Protocol + ``OpenAIProvider`` / ``AnthropicProvider``
(via Anthropic's OpenAI-compat endpoint) registry, ``data/pricing.yaml``
table for input/cached/output rates, ``compute_cost_usd_micro`` cost
math (micro-USD ints, no float drift), ``UsageRepo.record_turn``
writing one ``usage_events`` row per turn from a fresh tenant
transaction off the SSE critical path, ``ByoKeyStore`` JSON document
KMS-encrypted under ``EncryptionContext={"user_id": ...}`` with
narrow ``get_key`` accessor (plaintext leak point is one call
site), ``build_agent_for_user`` rewired to resolve provider →
build SDK ``Model`` from BYO API key + attach the bundled
``gh_*`` tool surface (issue list/create, PR view/comment/diff,
code search) when ``users.byo_provider_key_ct.github`` is present.
Phase 38 shipped 2026-04-29: ``src/lite_horse/observability/``
(``logs.py`` structlog JSON renderer + contextvars merge for
``request_id``/``user_id``/``session_key``/``turn_id``,
``tracing.py`` OTel SDK + OTLP HTTP exporter +
FastAPI/SQLAlchemy/httpx auto-instrumentation gated on
``OTEL_EXPORTER_OTLP_ENDPOINT``, ``metrics.py`` EMF JSON-line
helper); ``RequestIdMiddleware`` / ``LoggingMiddleware`` /
``MetricsMiddleware`` chained into ``create_app``; turn driver
emits ``turns_total`` / ``tokens_total`` / ``cost_usd_micro`` /
``errors_total`` and binds ``turn_id`` + ``session_key`` onto the
log context; scheduler tick emits ``cron_fires_total``;
scheduler / worker / api ``main`` entry-points now call
``configure_logging`` + ``configure_tracing``. ``infra/`` CDK
Python stack provisions VPC + ECS cluster + 3 services with ADOT
sidecar, RDS Postgres Multi-AZ, ElastiCache Redis, SQS, four S3
buckets (KMS-encrypted, audit-archive versioned + Glacier
lifecycle), Secrets Manager (DB / Redis / OpenAI / Anthropic /
JWKS / webhook HMAC), KMS CMK alias ``litehorse-{env}``, VPC
endpoints (S3 / Secrets Manager / KMS / SQS), CloudWatch
dashboard (turns/min, tokens/hr, cost/hr, ALB p95) + alarms
(ALB 5xx, DB connections, queue depth, EMF ``errors_total``).
``.github/workflows/{ci,deploy}.yml``: CI runs
ruff + mypy + alembic + pytest against PG/Redis services; deploy
builds + pushes ECR, runs ``alembic upgrade head`` as a one-shot
``ecs run-task`` gated on exit code 0, then forces a new
deployment per service with ``services-stable`` wait. 13 new
observability tests (log JSON shape, contextvars merge, EMF line
shape, OTel span via in-memory exporter, middleware behaviour
incl. ``X-Request-Id`` echo + JSON access lines + EMF
``http_requests_total`` / ``http_request_duration_ms``).
**v0.5 in flight; see active plan.**
Phase 39 shipped 2026-04-30: ``alembic 0002_phase39_user_limits``
adds ``users.rate_limit_per_min`` + ``users.cost_budget_usd_micro``
columns and ``ALTER TABLE ... FORCE ROW LEVEL SECURITY`` on the
four tenant-scoped tables; ``src/lite_horse/agent/mcp_pool.py``
implements an asyncio-locked TTL+LRU cache keyed on
``(user_id, slug, url)`` with ``MCPServerStreamableHttp.cleanup()``
on eviction/shutdown; ``src/lite_horse/web/rate_limit.py``
fixed-window Redis counter on ``rate:turn:{user_id}:{epoch_min}``
(60/min default, per-user override); ``src/lite_horse/web/cost_budget.py``
daily counter on ``cost:day:{user_id}:{YYYYMMDD}`` with NX-set
80% alert and 100% block raising ``cost_budget_exceeded``; both
preflight checks wired into ``POST /v1/turns`` + ``POST /v1/turns:stream``
and post-turn ``record_cost`` after ``UsageRepo.record_turn``;
``src/lite_horse/evolve/cloud.py`` ``EvolveMessage`` payload +
``find_evolve_candidates`` + ``run_evolve`` worker entry-point;
worker ``dispatch_message`` routes by ``is_evolve_payload``;
``src/lite_horse/scheduler/evolve_tick.py`` daily 86400 s tick
enqueues per (user x skill) candidates; ``docs/SECRET_ROTATION.md``
runbook with RDS-managed Lambda rotation, manual put-secret-value
flow, and emergency-revoke; ``tests/load/locustfile.py`` 100 users
x 10 turns/min profile; ``tests/security/test_rls_leak.py``
integration leak gate proving cross-tenant SELECT returns ``[]``
under a non-superuser app role with ``app.user_id`` GUC + RLS
``USING (user_id::text = current_setting(...))``. ``README.md``
rewritten around the cloud surface; ``docs/CLI.md`` flagged
dev-only; ``docs/EMBEDDING.md`` deprecated in favour of new
``docs/HTTP-API.md``. v0.4 plan flipped to **SHIPPED**.

## v0.5 — tenant-safe tools, multi-agent personas, evolution & recall — ☐ ACTIVE (2026-05-07)

**Active plan:** [plans/v0.5-tenant-evolve-recall.md](plans/v0.5-tenant-evolve-recall.md).
**Predecessor:** v0.4. **Background:** [HERMES_GAP_ANALYSIS.md](HERMES_GAP_ANALYSIS.md).

Closes the four gaps that block lite-horse from being the website
personal-assistant engine the product wants: (1) tenant-safe agent
tools — today `memory_tool`, `skill_manage`, `cron_manage`, plus the
`BudgetHook.consolidate` and `EvolutionHook` writes, all reach into
``litehorse_home()`` on the local container FS regardless of the
caller's `user_id`, so multi-task ECS leaks between tenants; (2)
multiple agents per user, the missing "agent management center" axis
(persona / model / tool-bundle / memory all per-agent); (3) a
Hermes-grade evolution layer (curator background pass + outcome
classifier + GEPA-style population evolve + user-skill→official
promotion) instead of today's regex-only refinement; (4) long-horizon
recall via pgvector + per-session summaries + cross-session memory
compaction. **Hard parity rule:** every cloud capability ships a
`*_local` backend so the `litehorse` Mac CLI keeps working
byte-for-byte against `~/.litehorse/` — phases include explicit "CLI
parity gate" acceptance items, and `tests/lint/test_cli_parity.py`
asserts every backend Protocol has both impls.

| #  | Subject | Status |
|----|---|---|
| 40 | Tool-backend abstraction + tenant-safe writes (BLOCKER)            | ✅ |
| 41 | Per-agent personas + agent CRUD                                    | ✅ |

### Blocked / in progress
Phase 40 shipped 2026-05-07: ``src/lite_horse/agent/backends/`` adds
``TenantContext`` + ``MemoryBackend`` / ``SkillBackend`` /
``CronBackend`` Protocols with both ``*_local.py`` (wrapping the v0.4
``MemoryStore`` / ``JobStore`` / local skills tree) and ``*_cloud.py``
(``MemoryRepo`` / ``CronRepo`` / ``SkillRepo`` per-call short-lived
``db_session(user_id)``). ``memory_tool`` / ``skill_manage`` /
``skill_view`` / ``cron_manage`` now resolve their backend off
``RunContextWrapper.context`` per turn; tool wire shapes are
unchanged. ``BudgetHook._consolidate`` and ``EvolutionHook``'s
distiller / refiner / ``record_outcome`` all flow through the same
backends; ``Runner.run(..., context=tenant)`` propagates the bundle
into side-agent runs. ``api.run_turn*`` build a local
``TenantContext``; ``web/turn_engine`` builds a cloud one keyed on
``req.user_id``. ``skills/stats.py`` is now path-based (caller passes
the skill dir) and the FS-touching helpers ``_view`` / ``dispatch``
moved to ``skills/local_view.py`` + ``skills/local_dispatch.py`` so the
agent layer stays free of ``litehorse_home`` / ``MemoryStore`` /
``skills_root`` / ``*Repo`` direct imports — enforced by
``tests/lint/test_no_litehorse_home_in_tools.py``.
``tests/lint/test_cli_parity.py`` asserts every Protocol has both
impls with the full method set. New
``tests/security/test_tool_tenant_isolation.py`` proves cross-user
writes don't leak through any of the three tools; new
``tests/cli/test_cli_byte_parity.py`` asserts local writes still
land at the v0.4 byte-shape paths.
Phase 41 shipped 2026-05-07: ``alembic 0003_phase41_agents`` creates
the ``agents`` table (per-user persona + ``default_model`` +
``permission_mode`` + ``enabled_tools`` JSONB + per-agent
``rate_limit_per_min`` / ``cost_budget_usd_micro`` + soft-delete
``archived_at``), adds ``users.default_agent_id`` and nullable
``agent_id`` to ``user_documents`` / ``skills`` / ``cron_jobs`` /
``sessions`` / ``skill_proposals`` / ``mcp_servers`` / ``commands`` /
``instructions``, backfills one default agent per existing user,
``SET NOT NULL`` on the user-only tables, ``CHECK (scope='user' AND
agent_id IS NOT NULL OR scope='official' AND agent_id IS NULL)`` on
the layered tables, and rewrites the RLS ``tenant_isolation`` policy
on ``user_documents`` / ``sessions`` / ``skill_proposals`` to the
compound ``user_id::text = current_setting('app.user_id', true) AND
(current_setting('app.agent_id', true) = '' OR agent_id::text =
current_setting('app.agent_id', true))``.
``src/lite_horse/storage/db.py::db_session(user_id, agent_id=None)``
now sets both GUCs; ``BaseRepo.current_agent_id()`` exposes the
agent GUC. ``models/agent.py`` + ``repositories/agent_repo.py`` own
list / get / create / update / archive / set_default / ensure_default
(the auto-create on first sight). New router
``web/routes/agents.py`` mounts ``/v1/users/me/agents`` with the
seven endpoints; ``TurnIn.agent_id`` and ``TurnRequest.agent_id``
flow into ``web/turn_engine.py`` which resolves the agent (request
body → ``users.default_agent_id`` → ``ensure_default``), opens
``db_session(user_id, resolved_agent_id)``, and threads
``agent_id`` through ``build_cloud_tenant_context``. Per-agent
overrides (``default_model`` / ``permission_mode``) shadow the
per-user defaults. Redis rate-limit + cost-budget keys gain an
``agent_id`` axis (``rate:turn:{user_id}:{agent_id}:{epoch_min}`` /
``cost:day:{user_id}:{agent_id}:{YYYYMMDD}``); per-agent
overrides shadow per-user limits. CLI ``litehorse agent {ls,
create, use, show}`` lays down ``~/.litehorse/agents/<slug>/``
mirrors of memory.md / user.md / skills/ / jobs.json, plus a
``current_agent`` selector file (``LITEHORSE_AGENT`` env override
takes precedence). New tests:
``tests/web/test_agents_api.py`` (HTTP CRUD + default-promotion +
archive guard), ``tests/security/test_agent_isolation.py``
(non-superuser cross-agent RLS leak gate), ``tests/cli/test_agent_cmd.py``
(CLI parity), ``tests/models/test_phase41_migration_static.py``
(static migration shape).
**v0.5 Phase 42 next.**
| 42 | pgvector recall + ``memory_search`` tool                           | ☐ |
| 43 | Session summaries + cross-session compaction                       | ☐ |
| 44 | Curator background pass + outcome classifier                       | ☐ |
| 45 | User-skill promotion + GEPA-style offline evolve                   | ☐ |
| 46 | Hardening: GDPR delete, audit shipper, SDK bumps, CLI parity gate  | ☐ |

---

## Naming convention for implementation plans

**Purpose:** a coding agent landing in this repo must be able to find "the
one plan I should execute against" in a single, deterministic hop. No guessing,
no grep ambiguity.

### Canonical paths

| Path | Role |
|---|---|
| `docs/PROGRESS.md` (this file) | status ledger only. One row per phase. No plan detail. Points to the active plan in its header. |
| `docs/plans/v<major>.<minor>-<kebab-slug>.md` | full plan detail. One file per version. Every file starts with `**Status:** ACTIVE \| SHIPPED \| DRAFT`. |
| `docs/EMBEDDING.md`, `docs/EVOLVE.md`, etc. | reference docs created by phases. **Not** plans. |

### Hard rules

1. **Exactly one ACTIVE plan at a time.** `grep '^\*\*Status:\*\* ACTIVE'
   docs/plans/*.md` must return exactly one match.
2. **No plans at `docs/` root.** The names `IMPLEMENTATION_PLAN.md`,
   `CURRENT_PLAN.md`, `TODO.md`, `ROADMAP.md`, `NEXT.md`, `PLAN.md` are
   **reserved to be absent**. Any plan goes in `docs/plans/`.
3. **Versioned filenames.** Format `v<major>.<minor>-<kebab-slug>.md`, slug
   ≤ 4 words, lowercase, kebab-case.
4. **Shipped plans are never renamed or moved.** External references (commits,
   PR descriptions, other docs) stay valid. Flip `Status: ACTIVE` →
   `Status: SHIPPED` in place, add a `**Shipped:** YYYY-MM-DD` line.
5. **Phase numbers never reset.** v0.1 used 0-12; v0.2 continues at 13+.
   Cross-version references ("Phase 9 was dropped in Phase 15") remain stable.
6. **Reference phases by number, not title.** Titles may drift while drafting.

### Workflow when starting v(N+1)

1. Create `docs/plans/v<N+1>-<slug>.md` with `**Status:** ACTIVE` and a
   `**Predecessor:**` link.
2. Flip the prior plan's header to `**Status:** SHIPPED` + shipped date.
3. Update the "Active plan:" link at the top of this file.
4. Append a new phase-status table below.

### Deterministic navigation for a coding agent

```
docs/PROGRESS.md
  → "Active plan:" link
  → docs/plans/v<N>-<slug>.md (Status: ACTIVE)
  → first ☐ phase matching your assignment
  → execute its Deliverables section, tick boxes, stop at its Acceptance gate
```

No other path is authoritative. Anything at `docs/` root that looks like a
plan is either a reference doc or a rule violation — fix the filing, don't
follow the file.
