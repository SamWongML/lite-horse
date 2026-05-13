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

> Tracked in [../../plans/v0.5/](../../plans/v0.5/README.md)
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
