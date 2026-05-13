# lite-horse v0.5 — tenant-safe tools, multi-agent personas, evolution & recall

**Status:** ACTIVE
**Started:** 2026-05-07
**Predecessor:** [v0.4-cloud-multi-tenant.md](../archive/v0.4-cloud-multi-tenant.md)
**Status ledger:** [../../PROGRESS.md](../../PROGRESS.md)
**Background research:** [../../research/hermes/](../../research/hermes/README.md)

---

## Reading order (for a phase session)

To keep session context small, **load only**:

1. This README (objective, non-goals, gates, deferred — ~250 lines).
2. The current phase file you're implementing (`phase-NN-*.md`, ~70–100 lines).
3. [`_contract.md`](_contract.md) — binding rules; read once per session.
4. [`_architecture.md`](_architecture.md) — only if your phase touches schema, tool Protocols, or `TenantContext`.
5. [`_research.md`](_research.md) — only the §sections your phase calls out.
6. [`_briefing.md`](_briefing.md) — only when dispatching a phase to a subagent.

Do **not** load sibling phase files or older `v0.X` plans unless cross-referenced explicitly.

---

## Objective

Close the four gaps that block lite-horse from being a credible
**website-personal-assistant engine** in the
"Claude managed agents"-style center the product wants to expose,
**while keeping the Mac single-user CLI experience byte-for-byte
unchanged**:

1. **Tenant-safe agent tools.** The agent's `memory`, `skill_manage`,
   and `cron_manage` tools (and the `BudgetHook.consolidate` /
   `EvolutionHook` writes that flow through them) currently write to
   `litehorse_home()` on the local container filesystem regardless of
   user. On a single ECS task this masquerades as working; on multi-task
   ECS, User A's writes either land in a file User B reads, or vanish
   on container restart. Read-injection comes from Postgres
   (`MemoryRepo`); writes go to a shared FS file. **This is the v0.5
   blocker.**
2. **Multiple agents per user.** The product surface is "manage many
   agents" (shopper / coder / writer / …), each with its own persona,
   default model, tool bundle, and memory. Today there is one agent
   per user; the data model has no `agents` table.
3. **Hermes-grade evolution.** Today's `EvolutionHook` only fires
   distillation on regex-detected error markers and only refines a
   single skill at a time. Hermes' Curator runs background passes that
   age, consolidate, and archive skills based on usage stats. The
   companion `hermes-agent-self-evolution` repo runs GEPA over a real
   eval set. Neither exists here.
4. **Long-horizon recall.** Memory is a flat 2 400-char `memory.md`
   plus a 1 500-char `user.md`, both injected wholesale. There is no
   semantic retrieval, no per-session summary surviving into the next
   session, no compaction when the cap is hit. For "what did we discuss
   about my Q3 plan three months ago?" the agent is blind.

The CLI from v0.3 / v0.4 stays as a **first-class developer surface** —
not deployed, but every cloud capability landed in v0.5 must also work
under `litehorse` on a Mac with `~/.litehorse/` as the source of truth.
The bar: `litehorse` from v0.4 keeps working byte-for-byte; new
capabilities (multi-agent personas, vector recall, curator) work in
both modes via the same code paths.

This is a smaller-scope release than v0.4 (no new infra primitive, no
new deploy shape) but a substantial behavior change at the agent /
storage seam. Estimate: ~4 500 net runtime LOC over v0.4.

## Non-goals (explicit cuts)

- **Full sandboxed shell execution.** Still out, as in v0.4. Hermes'
  7 execution backends (Modal / Daytona / Vercel Sandbox) are a v0.6
  candidate.
- **Messaging-channel gateways.** Still out. Hermes' 18 platform
  adapters are not the product surface.
- **Voice / wake-word / canvas / companion apps.** Still out.
- **Plan tier / billing model.** No `plan_tier` column, no per-tier
  caps. v0.6 candidate. Per-agent limits in Phase 41 are the only
  new quota axis.
- **External fine-tuning loop.** GEPA-style optimization in Phase 45 is
  prompt / skill text only; no LoRA, no model-level training.
- **Org / team / shared memory.** Memory namespacing in v0.5 is
  `(user_id, agent_id)`. Org tier is a v0.6 candidate.
- **WebSocket transport.** SSE stays the streaming surface. Bidirectional
  abort + replay land in Phase 46 over the existing HTTP shape.
- **Native cross-region replication.** v0.4 single-region stack is
  unchanged.
- **Automatic skill auto-merge.** Curator and GEPA in Phases 44–45
  produce **proposals** only. Approval stays human-gated.

---

## Phase index

Phases are sized for one focused subagent session each. Estimated runtime
LOC vs. tests in `+R / +T` format. Total v0.5 estimate: ~4 500 R + 3 500 T.

| Phase | File | Scope | LOC (R/T) |
|---|---|---|---|
| 40 — BLOCKER | [phase-40-tenant-tools.md](phase-40-tenant-tools.md) | Tool-backend abstraction + tenant-safe writes | +700 / +600 |
| 41 | [phase-41-personas.md](phase-41-personas.md) | Per-agent personas + agent CRUD | +800 / +600 |
| 42 | [phase-42-pgvector-recall.md](phase-42-pgvector-recall.md) | pgvector recall + `memory_search` tool | +700 / +500 |
| 43 | [phase-43-session-summaries.md](phase-43-session-summaries.md) | Session summaries + cross-session compaction | +500 / +400 |
| 44 | [phase-44-curator.md](phase-44-curator.md) | Curator background pass + outcome classifier | +700 / +600 |
| 45 | [phase-45-skill-promotion-gepa.md](phase-45-skill-promotion-gepa.md) | User-skill promotion + GEPA-style offline evolve | +800 / +600 |
| 46 | [phase-46-hardening.md](phase-46-hardening.md) | GDPR delete, audit shipper, SDK bumps, CLI parity gate | +300 / +400 |

## Shared references (load on demand only)

- [`_contract.md`](_contract.md) — Hard contract binding every implementation subagent.
- [`_architecture.md`](_architecture.md) — Locked architecture changes vs v0.4 (schemas, Protocols, GUCs).
- [`_research.md`](_research.md) — Binding research synthesis (Hermes, GEPA); per-phase §sections.
- [`_briefing.md`](_briefing.md) — Subagent dispatch preamble template.

---

## Cross-phase acceptance gates (extends v0.4)

Every phase must verify these before ticking its box. Items 1–8 are
inherited verbatim from the v0.4 plan; items 9–11 are new.

1. `uv run pytest -q` fully green; no `xfail` additions.
2. `uv run ruff check src tests` clean.
3. `uv run mypy src` clean (strict).
4. `make test-int` green (integration suite against docker-compose,
   includes pgvector from Phase 42 onward).
5. No code outside `src/lite_horse/storage/` imports `boto3`,
   `aiobotocore`, `redis`, or `aws_encryption_sdk`.
6. No code outside `src/lite_horse/repositories/` issues raw SQL.
7. `import lite_horse.api` does not transitively load `lite_horse.web`,
   `lite_horse.scheduler`, or `lite_horse.worker`.
8. LOC budget sanity: phase diff within ±25 % of estimate.
9. **No code under `agent/`, `memory/tool.py`, `skills/{manage,view}_tool.py`,
   `cron/manage_tool.py`, `skills/stats.py` imports `litehorse_home`,
   `MemoryStore`, `skills_root`, or any `*_repo` class directly.**
   Asserted by `tests/lint/test_no_litehorse_home_in_tools.py`.
10. **CLI parity gate green for the phase.** Every new capability runs
    end-to-end via `litehorse` against `~/.litehorse/`.
11. **Tenant leak test green for any new state-bearing table.**
    `tests/security/test_*_isolation.py` proves cross-user (and from
    Phase 41 onward, cross-agent) reads return 0 rows under RLS.

Total estimated runtime LOC added over v0.4: **~4 500 R + ~3 500 T**.
Estimated runtime budget after v0.5 lands: ~18 000 R (up from ~13 500).
These are ceilings.

---

## Risk register

| Risk | Mitigation |
|---|---|
| Phase 40's tool refactor changes JSON shapes by accident → bundled instructions reference dead names → silent agent quality regression | Tool wire shape is frozen by contract. `tests/agent/test_tool_wire_compatibility.py` snapshots every tool's JSON IO and fails CI on drift. |
| Phase 41's backfill on a large user base takes too long, blocks deploy | Backfill is one-shot in a separate ECS task with `pg_advisory_lock` and chunked `INSERT ... SELECT` (1 000 users per batch). Tested on a 100 k-user fixture in CI. |
| Phase 42 pgvector index build OOM on small RDS | HNSW build memory bounded by `maintenance_work_mem`; the migration sets it to `512 MB` for the duration of the index build. RDS instance class verified in CDK. |
| Phase 44 outcome classifier is too aggressive → false-failure refinement noise | Refinement requires `success_count + error_count >= 5` for a skill before any classifier signal can trigger it. Tunable in `lite_horse.constants`. |
| Phase 45 GEPA cost runs away on a popular skill | Per-run cost gate ($20 default), per-skill weekly cap, opt-in by `gepa: true` frontmatter. Cost charged to the originating user's budget. |
| CLI parity drift across phases | `tests/lint/test_cli_parity.py` runs on every push (Phase 40); upgraded to a hard end-to-end gate in Phase 46. |
| Anthropic / OpenAI SDK bump in Phase 46 breaks something deep | Bump deferred to its own phase with the budget to fix breakages. Smoke fixtures + manual run of the Phase 42–45 acceptance e2e tests gate the merge. |

---

## What v0.5 explicitly does NOT do (deferred to v0.6+)

These are items from the gap analysis that are real but out of scope:

- **Org / team / shared memory.** Memory namespace stays
  `(user_id, agent_id)`.
- **Plan tier / billing model.** No `plan_tier` column.
- **WebSocket transport.** SSE only.
- **Native shell / code-exec sandbox.** Hermes' Modal / Daytona /
  Vercel Sandbox backends are a v0.6 candidate.
- **Native cross-region replication.**
- **Fine-tuning / LoRA loop.** GEPA stays prompt-text only.
- **Skill-marketplace public surface.** Promotion is admin-gated only.
- **Agent handoff / multi-agent collaboration.** Each turn runs one
  agent; subagents stay internal (Consolidator, distiller, refiner,
  curator-reviewer, summarizer, outcome-classifier).
- **PII redaction in `audit_log` content fields.** Phase 46 anonymises
  `actor_id` only on GDPR delete; other PII handling is deferred.
- **Native local Ollama integration in the CLI.** The provider
  Protocol allows it but no first-party impl ships.
