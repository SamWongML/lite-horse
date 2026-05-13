# Research synthesis (binding — do not re-research)

> Part of v0.5. Load this only when starting a phase whose subsection here applies (Phase 40 → §Memory provider; Phases 42/43 → §Recall; Phases 44/45 → §Curator / GEPA; Phase 41 → §Multi-agent).

Source documents — re-read before starting any phase:

- **[`docs/research/hermes/`](../../research/hermes/README.md)** —
  full Hermes-vs-lite-horse comparison; the §3, §4, §5, §9 sections
  drive Phase 40, 44/45, 42/43, and 41 respectively.
- **NousResearch/hermes-agent** at commit-of-record 2026-05-07.
  Curator: `agent/curator.py` (`use_count` / `view_count` /
  `patch_count` / `last_activity_at` in `~/.hermes/skills/.usage.json`;
  `active → stale → archived` transitions; auxiliary-model review
  proposing consolidations and drift patches; only touches
  `created_by: 'agent'` skills).
  Memory provider Protocol: `agent/memory_provider.py` with
  `sync_turn(turn_messages)` / `prefetch(query)` / `shutdown()` /
  optional `post_setup(hermes_home, config)` hooks. Drives the
  `MemoryBackend` shape in Phase 40.
- **NousResearch/hermes-agent-self-evolution** (ICLR 2026 Oral, MIT)
  — DSPy + GEPA loop: read defs → generate eval set from execution
  traces → GEPA produces variants → evaluate → constraint gates
  (tests, size, benchmarks) → emit a PR for human review. Cost
  $2-10/run, no GPU. Drives Phase 45.

### Calibration against current code (verified 2026-05-07)

- RLS is **already on** in production: `0001_initial_schema.py:535`
  enables row-level security on `messages`, `sessions`,
  `user_documents`, `skill_proposals`; `0002_phase39_user_limits.py:43`
  adds `FORCE ROW LEVEL SECURITY`. v0.5 does **not** re-enable RLS —
  it only re-verifies that any new tenant-scoped table introduced
  here gets the same treatment.
- `web/turn_engine.py:81` already opens `db_session(req.user_id)` and
  passes `user_id=req.user_id` into `build_agent_for_user`, but neither
  the `RunContextWrapper.context` nor any of the agent's tools see it.
  Fixing that is the lever Phase 40 pulls.
- `EvolutionHook` (`agent/evolution.py`) already exists with distiller
  + refiner side-agents. Phase 44–45 extend it; they do not replace it.
- `Consolidator` (`agent/consolidator.py`) already exists; Phase 40
  redirects its `MemoryStore.add` writes through the new backend.
- The `OpenAI Agents SDK ≥0.14` exposes `RunContextWrapper.context`
  as a free-form generic — that is the supported channel for
  per-turn tenant context. Do not reach for `contextvars` inside
  tools; the SDK already serialises tool calls per turn.

