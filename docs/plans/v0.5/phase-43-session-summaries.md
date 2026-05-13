# Phase 43 — Session summaries + cross-session compaction (+500 / +400 LOC)

> Part of v0.5. See [README.md](README.md) for objective/non-goals, [_contract.md](_contract.md) for binding rules, [_architecture.md](_architecture.md) for shared types, [_briefing.md](_briefing.md) for the subagent briefing template.

**Objective.** Turn each completed session into a 1–3 sentence summary
that survives into the next session, and compact `memory.md` when it
hits the cap so writes stop silently failing.

**Deliverables.**

- Alembic `0006_phase43_summaries.py`: `session_summaries` table per
  the delta.
- `models/session_summary.py`, `repositories/session_summary_repo.py`.
- `agent/summarizer.py` — small side-agent (3-turn budget, model
  configurable, default `gpt-5.4-mini` or `claude-haiku-4-5`) that
  reads the last N=20 messages of a session and emits
  `{topic: str, summary: str (≤300 chars)}`.
- Trigger: at session end (any of: explicit `POST /v1/sessions/{id}:end`,
  or scheduler tick observing `sessions.ended_at IS NULL` and
  `last_message_ts < now() - interval '24 hours'`). On fire, enqueue a
  `summarize` SQS message routed to the worker.
- Worker gains a `summarize` handler: runs `agent/summarizer.py`,
  writes to `session_summaries`, and indexes the summary into
  `memory_chunks` via the Phase 42 backend.
- Prompt assembly (`agent/instructions.py`):
  - Inject the **last 3 session summaries** for the current agent
    (most-recent-first) into the system prompt under a
    `## Recent Sessions` block.
  - On a new session, **vector-retrieve** the top-3 most relevant past
    summaries based on the user's first message and inject them under
    `## Relevant Past Sessions`.
- Cross-session memory compaction: a daily per-user-per-agent cron
  enqueues a `compact` task whenever
  `MemoryRepo.utilization('memory.md') > 0.8`. Worker runs the
  v0.4 `Consolidator` against the existing `memory.md` content with
  instructions to merge similar entries and drop transient ones.
  Output replaces the file (cloud: one transaction; local: atomic
  rename) and is embedded into `memory_chunks` for recall.
- CLI:
  - Local sessions get summarised on `litehorse` REPL exit (Ctrl-D)
    or on `litehorse sessions end <key>`.
  - Compaction runs on `litehorse memory compact` or auto on the same
    80% threshold.

**Acceptance.**

- A 3-session sequence: session A discusses topic X; session B
  discusses topic Y; session C asks "what did we cover earlier?" and
  the agent's prompt contains both summaries. Covered by
  `tests/agent/test_session_summary_injection.py`.
- Compaction: a `memory.md` that has hit its cap and raised
  `MemoryFull` once is reduced under 80% within one compact run, with
  no fact loss as scored by the v0.2 `agent/errors.py` classifier.
- **CLI parity gate.** Run a 3-session local sequence on Mac; assert
  `~/.litehorse/agents/default/sessions/` and the `chromadb`
  embeddings reflect the same shape as the cloud test.
- Cross-phase gates 1–8 pass.
- `docs/PROGRESS.md` v0.5 row 43 flipped to ✅.

