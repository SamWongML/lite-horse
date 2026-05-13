# Phase 44 — Curator background pass + outcome classifier (+700 / +600 LOC)

> Part of v0.5. See [README.md](README.md) for objective/non-goals, [_contract.md](_contract.md) for binding rules, [_architecture.md](_architecture.md) for shared types, [_briefing.md](_briefing.md) for the subagent briefing template.

**Objective.** Make skill quality self-maintaining over weeks of
operation, not just per-turn. Replace the regex-only error detection
in `EvolutionHook` with a real signal pipeline.

**Deliverables.**

- Alembic `0007_phase44_curator.py`:
  - `turn_outcomes` table per delta.
  - Add `use_count`, `success_count`, `error_count`, `last_used_at`,
    `curator_state` to `skills`.
- `models/turn_outcome.py`, `repositories/turn_outcome_repo.py`.
- `agent/backends/feedback.py` — `FeedbackSink` Protocol:
  `record(turn_id, source, rating, reason=None)`.
  - `feedback_cloud.py` → `TurnOutcomeRepo`.
  - `feedback_local.py` → NDJSON line append to
    `~/.litehorse/feedback.log`.
- `agent/outcome_classifier.py` — small side-agent that runs at end of
  every turn (in the worker for cloud; inline for CLI), reads the
  final assistant text + tool outputs, classifies as
  `success | partial | failure`, writes a `turn_outcomes` row with
  `source='classifier'`. 2-turn budget; never blocks the turn (cloud
  path enqueues `classify` SQS message; local runs after
  `streaming.stream_events()` exhausts).
- `web/routes/turns.py` gains
  `POST /v1/turns/{turn_id}/feedback` (`source='user_explicit'`).
- `EvolutionHook` (`agent/evolution.py`) extended: in addition to
  regex error markers, on `on_end` it consults the most recent
  `turn_outcomes` row for the just-finished turn (cloud) or the most
  recent NDJSON line (local) and treats `rating=-1` as a failure
  signal that triggers refinement of the most recently viewed skill.
- `agent/curator.py` (NEW) — daily-per-user-per-agent job:
  - Reads all `(user_id, agent_id)` skills with their stats.
  - Transitions states:
    - `last_used_at < now() - interval '90 days'` AND
      `success_count = 0` → `archived`.
    - `last_used_at < now() - interval '30 days'` → `stale`.
    - Pinned skills (frontmatter `pinned: true`) never transition.
  - Spawns an auxiliary-model side-agent ("curator-reviewer") to
    propose **consolidations**: when two skills overlap >70% in
    embedding cosine, propose a merge (one `skill_proposals` row
    superseding both).
  - All proposals go to `skill_proposals`, never auto-merged.
- Scheduler tick (`scheduler/curator_tick.py`) enqueues `curate`
  per `(user_id, agent_id)` daily.
- Worker gains `curate` and `classify` handlers.
- CLI:
  - `litehorse skills curate` runs the curator pass locally against
    `~/.litehorse/agents/<slug>/skills/`.
  - `litehorse feedback <turn_id> --rating -1` writes to
    `feedback.log`; subsequent `litehorse skills evolve` honors it.

**Acceptance.**

- Curator daily run: 30-day-old unused skill auto-transitions to
  `stale`; 90-day-old unused with no successes auto-transitions to
  `archived`; `curator_state='archived'` skills are excluded from
  the agent's skill index in the system prompt.
- A regression test (`tests/agent/test_curator_consolidate.py`): two
  near-identical skills (cosine > 0.85) produce a single consolidation
  proposal in `skill_proposals` within one curator run.
- Outcome classifier accuracy on a 50-turn fixture set is ≥85% vs.
  hand-labels.
- A user posting `feedback rating=-1` triggers the refiner side-agent
  on the next agent build for that skill.
- **CLI parity gate.** Run `litehorse skills curate` on a seeded local
  state with two near-identical skills; observe a proposal under
  `~/.litehorse/agents/default/skills/.proposals/`.
- Cross-phase gates 1–8 pass.
- `docs/PROGRESS.md` v0.5 row 44 flipped to ✅.

