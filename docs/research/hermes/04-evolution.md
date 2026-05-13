# §4. Evolution gaps vs. Hermes

> Drives v0.5 Phases 44 (Curator) and 45 (GEPA / promotion). See [README.md](README.md).

lite-horse has the right shape (distiller-on-success / refiner-on-failure
hooks + offline evolve worker) but stops well short of the reference.

### 4.1 No Curator equivalent

Hermes' `agent/curator.py` is a background process that periodically:

- transitions skills `active → stale → archived` based on `last_activity_at`
- spawns an auxiliary-model review proposing **consolidations** (merge
  redundant skills) and **drift patches**
- only touches `created_by: 'agent'` skills (bundled stay frozen)
- exposes a user-facing `pin` to lock skills

lite-horse has a `skill_proposals` table but no scheduled curator pass that
*generates* proposals from usage stats. `skills/stats.py` records outcomes
but nothing acts on them at the corpus level.

**Missing puzzle**: a curator job (already-have `cron_jobs` infra is the
right place) that runs daily per user, reads `skill.usage_count`,
`success_count`, `error_count`, age, and emits consolidation /
archival / patch proposals. Auto-archive after N days unused.

### 4.2 Offline evolve worker is single-skill, single-pass

`evolve/runner.py` proposes one revision for one skill from recorded
failures. The Hermes self-evolution sister repo runs **GEPA** (Genetic-Pareto
Prompt Evolution) with:

- read defs → generate eval set from execution traces
- GEPA produces variants
- evaluate each on the eval set
- constraint gates (tests, size, benchmarks)
- emit a PR for human review

The cost is reportedly $2-10/run, no GPU. Adding this to the daily evolve
queue would close the most distinctive Hermes feature gap.

**Missing puzzle**: an eval-set generator that mines execution traces
(SQS → S3 archive of failed turns), a fitness function that doesn't only
count "did the next turn pass," and population-level optimization rather
than single-shot.

### 4.3 No skill-promotion / cross-user knowledge sharing

A skill that User A discovered which would help every user never gets
seen by anyone else. The `bundled → official → user` precedence has the
slot for it (the "official" tier), but no mechanism promotes a
high-quality user skill into "official" candidates.

**Missing puzzle**: an admin queue that surfaces frequently-successful
user skills as candidates for `official` promotion (with ToS / privacy
review). Could be as simple as a daily aggregator that ranks user skills
by `unique_user_count × success_rate × use_count`.

### 4.4 No reflection on conversation outcome

Hermes' Curator and the in-loop skill creation both react to *trajectories*.
lite-horse's `EvolutionHook` only fires distillation when `tool_call_count
>= 5`, and only fires refinement when an explicit error marker matches a
regex (`"success": false`, `traceback`, etc.). Many real failures are
silent — model says "I tried but couldn't" without an error marker.

**Missing puzzle**: an outcome classifier that's separate from regex
matching — either a small LLM grader call at end-of-turn, or a thumbs-up /
thumbs-down feedback API that a calling website can post per turn.

### 4.5 No tool / prompt evolution, only skill evolution

Hermes' GEPA loop optimizes **tool descriptions** and **system prompts**
in addition to skills. lite-horse evolves only `SKILL.md` content. The
bundled instructions in `bundled/instructions/` are static.

**Missing puzzle**: extend the offline evolve target set to include
instruction blocks (per user, layered) and tool description strings.

### 4.6 No fitness eval set

Both repos lack a built-in eval harness in the agent core. Hermes punts
this to the sister repo. lite-horse has nothing. Without an eval set,
none of the evolution proposals can be scored at population level.

**Missing puzzle**: per-skill golden evals (a small set of `(input,
expected_outcome)` cases per skill, stored in `skill_proposals` or a new
`skill_evals` table). Required for any non-trivial GEPA-style loop.

