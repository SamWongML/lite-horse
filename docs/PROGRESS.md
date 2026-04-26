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
| 31 | Foundations: storage layer + ORM + Alembic + FastAPI skeleton    | ☐ |
| 32 | SessionDB port to Postgres                                       | ☐ |
| 33 | Layered config: user-scope CRUD + effective-config resolver      | ☐ |
| 34 | Admin layer: official-scope CRUD, versioning, audit, cache inval | ☐ |
| 35 | Streaming + permissions + idempotency                            | ☐ |
| 36 | Scheduler + worker services, org-wide cron                       | ☐ |
| 37 | Multi-provider, KMS-encrypted BYO keys, cost meter, GitHub tools | ☐ |
| 38 | Observability, IaC, deploy pipeline                              | ☐ |
| 39 | Hardening: RLS, secret rotation, MCP pool, evolve, load + leak   | ☐ |

### Blocked / in progress
Phase 31 not started. Open questions in plan §"Open questions" must be
resolved before kickoff.

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
