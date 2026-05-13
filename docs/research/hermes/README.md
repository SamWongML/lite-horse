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

## Section index — load only the one(s) your phase references

| § | File | Drives |
|---|---|---|
| 3 | [03-multi-tenancy-bug.md](03-multi-tenancy-bug.md) | v0.5 Phase 40 |
| 4 | [04-evolution.md](04-evolution.md) | v0.5 Phases 44, 45 |
| 5 | [05-memory.md](05-memory.md) | v0.5 Phases 42, 43 |
| 6 | [06-multi-tenancy-rls.md](06-multi-tenancy-rls.md) | v0.5 Phase 46 (partial) |
| 7 | [07-agent-loop.md](07-agent-loop.md) | deferred (v0.6+) |
| 8 | [08-operational.md](08-operational.md) | deferred |
| 9 | [09-agent-management-surface.md](09-agent-management-surface.md) | v0.5 Phase 41 |
| 10 | [10-smaller-items.md](10-smaller-items.md) | mixed |

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

## 11. Prioritized punch list

Top-down, rough effort estimates. v0.5 status (shipped 2026-05-14)
in line-leading tags.

### Tier 1 — required before any production deploy

1. ✅ **§3** Fix the FS-vs-DB write asymmetry. Migrate `memory_tool`,
   `skill_manage`, `cron_manage`, `BudgetHook._consolidate`, and
   `EvolutionHook` skill IO to per-user repos. — **Phase 40.**
2. ✅ **§5.3 + §9** Introduce `agents` table with persona, default
   model, tool bundle. Add agent CRUD. Pivot session ownership by
   agent. — **Phase 41.**
3. ◐ **§6.1** Extend RLS coverage to `skills`, `cron_jobs`,
   `commands`, `instructions`, `mcp_servers`, `usage_events`,
   `audit_log`. — **Phases 41–44** added RLS + FORCE + compound
   `(user_id, agent_id)` policy on every new state-bearing table
   (`agents` / `memory_chunks` / `session_summaries` /
   `turn_outcomes`); legacy-table RLS back-fill on
   `usage_events` / `audit_log` is **deferred to v0.6** (admin
   surfaces still read them without an `app.user_id` GUC).

### Tier 2 — closes the Hermes-feature gap

4. ✅ **§5.1** pgvector + `memory_chunks` + `memory_search` tool. —
   **Phase 42.**
5. ✅ **§4.1** Curator background job — daily per-user pass over
   skill stats producing consolidation/archive/patch proposals. —
   **Phase 44.**
6. ✅ **§5.2** Per-session summaries with vector retrieval into
   prompt. — **Phase 43.**
7. ✅ **§4.4** Outcome classifier — small LLM grader at end-of-turn
   plus the `POST /v1/turns/{turn_id}/feedback` API the website
   calls. — **Phase 44.**

### Tier 3 — differentiation and polish

8. ✅ **§4.2** Population-level (GEPA-style) offline evolve loop
   with a real fitness eval set (§4.6). — **Phase 45.**
9. ✅ **§4.3** User-skill → official-skill promotion queue. —
   **Phase 45** (admin-only `/v1/admin/skill-candidates` + daily
   tick aggregating cross-tenant by `frontmatter.name`).
10. ☐ **§7.3** Attachment ingestion, calendar tools, email tools.
    — **Deferred to v0.6.**
11. ✅ **§5.4** Cross-session memory compaction job. — **Phase 43**
    (worker `compact` tick gated on `memory.md` utilisation > 0.8).
12. ☐ **§9** WebSocket alternative; replay endpoint; per-agent
    session grouping in UI. — **Deferred to v0.6.**
13. ☐ **§6.4 / §6.5** Multi-axis quotas and tiered plans. —
    **Deferred to v0.6.** Phase 41 added the per-agent quota axis
    but no `plan_tier` column.

### Tier 4 — operational hygiene

14. ◐ **§8** Migration step in deploy pipeline (✅ v0.4 — `alembic
    upgrade head` as a one-shot `ecs run-task` gated on exit 0);
    restore runbook / canary / load + capacity tests — **deferred
    to v0.6.**
15. ✅ **§6.3** GDPR delete pipeline. — **Phase 46**
    (`gdpr_delete_requests` table, `:request-delete` /
    `:cancel-delete` routes, daily worker that exports to S3,
    purges every tenant table in one tx, and tombstones
    `audit_log.actor_id`).
16. ✅ **§10** SDK pin bumps (`openai>=2.5,<3`,
    `openai-agents>=0.16,<0.18`); model-id constants
    (`constants/models.py`); HSTS/CSP middleware
    (`web/middleware/security_headers.py`); audit-archive shipper
    (daily worker uploads `audit_log` rows older than 90 days as
    JSONL and `DELETE`s them from PG). — **Phase 46.**

### Deferred to v0.6

Items the v0.5 punch-list scored but did not close — re-graded out
of Phase 46:

- **§6.1 (legacy-table RLS back-fill)** for `usage_events` and
  `audit_log`. Both are admin-read surfaces today; promoting them
  to compound-policy RLS requires the admin layer to bind a
  per-target `app.user_id` GUC or switch to an explicit
  superuser role for the cross-tenant scans.
- **§7.3** Attachment ingestion, calendar tools, email tools.
- **§9** WebSocket transport, replay endpoint, per-agent session
  grouping in the webapp UI.
- **§6.4 / §6.5** `plan_tier` column + tiered per-tier quotas.
- **§8 (operational hygiene tail)** restore runbook, canary
  rollouts, scripted load tests + capacity model.
- **§7** agent-loop refactors (planner / executor split,
  attempt/repair loop, native shell sandbox).
- **§8 (deploy tail)** PITR drill cadence, infra cost dashboard.

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
