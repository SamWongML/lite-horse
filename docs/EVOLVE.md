# The offline evolve loop

`lite_horse.evolve` is a separate, **admin-invoked** pipeline that proposes
revisions to a skill's `SKILL.md` based on how that skill has been performing
in recorded sessions. It never edits the live skill — it writes *proposals*
to disk for a human (or the webapp's admin UI) to approve.

The module lives outside the runtime import graph. `import lite_horse.api`
must not transitively load `lite_horse.evolve`; a test enforces this.

---

## When to run it

- A skill has accumulated real failures in `sessions.db` (see the stats
  sidecar `error_count`).
- You see a skill's `last_error_summary` repeating the same shape across
  distinct sessions.
- The webapp's admin UI has surfaced the skill as "regressing" (fragile
  decay tag — success ratio < 50% over ≥ 3 uses).

Running it against a skill with zero failure traces is a no-op at worst.

---

## How to invoke

```bash
python -m lite_horse.evolve <skill-slug> [--days 14]
```

Prints the `EvolveResult` as JSON and exits `0` on approval, `1` otherwise.
`--days` bounds how far back the trace miner looks (default: 14).

Library form (for webapp background workers):

```python
from lite_horse.evolve import evolve

result = evolve("my-skill", days=14)
if result.approved:
    # result.proposal_path and result.sidecar_path exist under
    # ~/.litehorse/skills/.proposals/<skill>/<ts>.{md,json}
    ...
```

Every dependency is injectable — the signature accepts `reflector_fn`,
`judge`, `embedder`, and `pytest_runner` — so tests (and the webapp's own
test harness) can drive the loop without calling OpenAI.

---

## Pipeline shape

```
SessionDB + skill stats
        │
        ▼
    trace_miner  ──►  up to 5 failure trajectories (task, response, outcome)
        │
        ▼
    reflector    ──►  one revised SKILL.md candidate
        │
        ▼
┌── constraints (hard gates, short-circuiting) ──┐
│   size ≤ 15 KB                                  │
│   no injection patterns / invisible Unicode     │
│   frontmatter: valid YAML, name unchanged,      │
│                version bumped                   │
│   pytest suite passes                           │
└─────────────────────┬───────────────────────────┘
                      ▼
               fitness scoring
                      │
                      ▼
            cosine ≥ 0.75 vs baseline
                      │
         ┌────────────┴────────────┐
     approved                  rejected
         │                        │
         ▼                        ▼
  .proposals/<name>/<ts>.md   EvolveResult(approved=False, reason=...)
  .proposals/<name>/<ts>.json
```

Gates are short-circuited in module order: if `size` fails we don't bother
running pytest. Cosine is evaluated last because it needs embedding calls.

---

## Gate reference

| Gate | Source | Fails when |
|---|---|---|
| `size` | `SKILL_MAX_BYTES = 15_360` | Candidate > 15 KB (UTF-8). |
| `injection` | `security.validators.check_untrusted` | Text matches a known jailbreak pattern or contains invisible Unicode. |
| `frontmatter` | `evolve.constraints.check_frontmatter` | Missing/invalid YAML, `name:` changed, or `version:` not an int `>= baseline_version + 1`. |
| `pytest` | `evolve.constraints.check_pytest` | Injected runner returns `False` or crashes. Default runner spawns `pytest -q -x --no-header`. |
| `cosine` | `fitness.score` | `text-embedding-3-small` cosine between candidate and baseline < 0.75 — catches purpose drift. |

A candidate must pass **every** gate. There is no partial approval.

---

## Fitness scoring

Reported on the sidecar JSON but not used for gating (except `cosine`, which
has its own threshold gate). The webapp can sort / rank pending proposals by
`total`.

```
total = max(0, judge - length_penalty) * 0.7 + cosine * 0.3
```

- `judge` — LLM rubric in `[0, 1]`. Default model: `gpt-4o-mini`.
- `length_penalty` — linear 0 → 0.2 as size approaches `SKILL_MAX_BYTES`.
- `cosine` — embedding similarity against the baseline.

---

## Proposal on disk

```
~/.litehorse/skills/.proposals/<skill>/<YYYYMMDDTHHMMSS>.md
~/.litehorse/skills/.proposals/<skill>/<YYYYMMDDTHHMMSS>.json
```

The `.md` is a full `SKILL.md` — ready to copy into
`~/.litehorse/skills/<skill>/SKILL.md` on approval.

The `.json` sidecar is the audit trail:

```json
{
  "skill": "my-skill",
  "created_at": "20260423T180000",
  "fitness": {"judge": 0.85, "length_penalty": 0.0, "cosine": 0.92, "total": 0.87},
  "gates":   {"size": {"passed": true, "reason": ""}, ...},
  "trajectories": [
    {"session_id": "...", "task": "...", "response": "...", "outcome": "tool_error"}
  ]
}
```

`trajectories` is the exact evidence the reflector saw. A reviewer can read
it before deciding to merge.

---

## Approval workflow (webapp side)

1. List `.proposals/<skill>/*.md` that are newer than the current
   `SKILL.md`'s `version:`.
2. Render the `.json` sidecar (gates + fitness + trajectories) alongside a
   diff of `.md` vs the live `SKILL.md`.
3. On approve:
   ```python
   from shutil import copy2
   from lite_horse.skills.stats import mark_optimized

   copy2(proposal_md, skills_root() / skill / "SKILL.md")
   mark_optimized(skill)   # stamps last_optimized_at in the stats sidecar
   ```
4. On reject: delete or archive the `.md` and `.json` pair; no further state
   to update.

`mark_optimized` is the only stats-sidecar write outside the runtime path —
keep it there so the runtime can read it as a signal for conditional
activation (Phase 21) and future decay heuristics.

---

## Intentionally not included

- **No DSPy / GEPA runtime dep.** The reflector is straight `openai` calls.
  If we later want GEPA-style multi-pass search, it goes behind the same
  `Reflector` interface — no webapp change needed.
- **No auto-approval.** Proposals never merge themselves. The human (or the
  webapp) is always in the loop.
- **No online learning.** Skills are never mutated mid-run. Evolution is
  strictly offline and strictly batched.
