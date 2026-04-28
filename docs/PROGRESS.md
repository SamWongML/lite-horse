# lite-horse — phase status

**Active plan:** [plans/v0.4-cloud-multi-tenant.md](plans/v0.4-cloud-multi-tenant.md)

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

## v0.4 — cloud multi-tenant service — ☐ ACTIVE (started 2026-04-26)

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
| 38 | Observability, IaC, deploy pipeline                              | ☐ |
| 39 | Hardening: RLS, secret rotation, MCP pool, evolve, load + leak   | ☐ |

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
Phase 38 (observability, IaC, deploy pipeline) is next.

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
