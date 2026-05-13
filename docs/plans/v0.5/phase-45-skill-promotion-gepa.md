# Phase 45 — User-skill promotion + GEPA-style offline evolve (+800 / +600 LOC)

> Part of v0.5. See [README.md](README.md) for objective/non-goals, [_contract.md](_contract.md) for binding rules, [_architecture.md](_architecture.md) for shared types, [_briefing.md](_briefing.md) for the subagent briefing template.

**Objective.** Let high-quality user skills become candidate `official`
content (with admin gating), and replace single-shot reflection in
`evolve/` with a population-level loop on a real eval set.

**Deliverables.**

- Alembic `0008_phase45_promotion.py`: `skill_promotion_candidates`
  table per delta.
- `repositories/skill_promotion_repo.py`.
- `scheduler/promotion_tick.py` — daily aggregator across the entire
  user base (admin context, RLS bypassed via SECURITY DEFINER helper):
  for each unique skill `frontmatter.name`, compute
  `unique_user_count`, `use_count`, `success_rate`. If thresholds met
  (constants in `lite_horse.constants`), upsert a row in
  `skill_promotion_candidates`.
- `web/routes/admin.py` extended:
  - `GET /v1/admin/skill-candidates` — paginated list, sortable.
  - `POST /v1/admin/skill-candidates/{id}:promote` — clones the
    user's skill body into the `official` scope as a new version
    (`created_by=admin_user_id`); writes `audit_log`; marks the
    candidate `status='promoted'`.
  - `POST /v1/admin/skill-candidates/{id}:reject` — marks
    `status='rejected'` with optional `reason`.
- `evolve/gepa/` — population-level optimization:
  - `eval_set.py`: mines `turn_outcomes` for the target skill,
    pulls 10–50 trajectories with rating ≠ 0, packages each as
    `(user_request, expected_outcome_signal)` cases.
  - `population.py`: generates K=8 variants per generation by asking
    a generator model to mutate the SKILL.md (rephrase Procedure,
    add Pitfalls entries, reorder steps). Diversity gate: any two
    variants with embedding cosine > 0.95 are pruned.
  - `fitness.py`: extends v0.2 `evolve/fitness.py` — for each variant
    run the agent against each eval case, score via the outcome
    classifier from Phase 44, return mean rating + variance.
  - `runner.py`: runs N=3 generations, keeps Pareto frontier
    (rating vs. SKILL.md size), emits the best variant as a
    `skill_proposals` row with `fitness` JSONB populated.
- Cost gate: `evolve/gepa/runner.py` aborts if total token cost
  estimate > $20 (configurable). Default schedule: opt-in per skill
  via `frontmatter: gepa: true`.
- Worker gains `evolve_gepa` message type. Scheduler enqueues weekly
  per opted-in skill.
- CLI:
  - `litehorse skills evolve <slug> --population` runs locally.
  - Defaults to OpenAI / Anthropic provider already configured;
    local Ollama can be plumbed via the v0.4 `ModelProvider`
    Protocol but is out-of-scope test surface.

**Acceptance.**

- A seeded user skill with 10 sample trajectories produces a
  population of 8 variants and a winning Pareto-frontier proposal in
  `skill_proposals` within one `evolve_gepa` worker run, end-to-end
  cost <$10 on `gpt-5.4-mini`.
- Admin promotes a candidate; the next user turn for an unrelated
  user has the now-`official` skill in their `EffectiveConfig`.
- Privacy gate: promotion endpoint requires `role='admin'` and writes
  an `audit_log` row with the source `(user_id, skill_id)` redacted
  unless the source user opted in via `agents.share_skills=true`.
- **CLI parity gate.** `litehorse skills evolve <slug> --population
  --generations 1 --population-size 4` runs locally on a small fixture
  in <2 min and writes a proposal under
  `~/.litehorse/agents/default/skills/.proposals/`.
- Cross-phase gates 1–8 pass.
- `docs/PROGRESS.md` v0.5 row 45 flipped to ✅.

