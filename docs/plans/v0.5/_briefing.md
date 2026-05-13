# Subagent briefing template

> Part of v0.5. Use when dispatching a phase to a subagent. Quotes [_contract.md](_contract.md).

Every implementation subagent is dispatched with this preamble.
**Do not deviate.** If a constraint here conflicts with what you think
is "cleaner", stop and escalate to the orchestrator.

```
You are implementing Phase <N> of lite-horse v0.5 per
docs/plans/v0.5/phase-<N>-*.md.

PRE-WORK (mandatory):
  1. Read docs/plans/v0.5/_contract.md (Hard contract) + your phase file in full.
  2. Read docs/plans/archive/v0.4-cloud-multi-tenant.md's "Hard contract" — it still binds.
  3. Read the docs/plans/v0.5/_research.md sections referenced from your phase,
     and only the docs/research/hermes/§N files those research bullets link to.

HARD CONSTRAINTS (non-negotiable, from v0.5 plan's "Hard contract"):

1. Tool / hook backend abstraction. Every tool that touches durable
   state goes through agent/backends/<X>.py Protocols. No tool body
   imports litehorse_home, MemoryStore, skills_root, or any *_repo
   class directly. Lint asserted by
   tests/lint/test_no_litehorse_home_in_tools.py.

2. CLI parity. Every new capability ships a *_local backend with
   the SAME method set as *_cloud. The litehorse CLI must keep
   working byte-for-byte against ~/.litehorse/ throughout. Your
   phase's acceptance section names the parity gate; wire it into
   tests/cli/ before declaring done.

3. Per-agent scope (from Phase 41 onward). Every tenant-scoped
   table you add carries (user_id, agent_id). RLS policy is
   user_id::text = current_setting('app.user_id', true)
     AND (current_setting('app.agent_id', true) = ''
          OR agent_id::text = current_setting('app.agent_id', true)).
   db_session(user_id, agent_id) sets both GUCs.

4. Tool wire shape is FROZEN. Tool function names, argument names,
   and JSON output shapes for memory / skill_manage / skill_view /
   cron_manage / session_search / memory_search (from Phase 42)
   cannot change. Frontmatter shape for SKILL.md is FROZEN.

5. Everything in v0.4's Hard Contract still holds: tenancy,
   storage abstraction layers, layered config (bundled / official /
   user), versioning, auth, async discipline, encryption at rest,
   migrations (Alembic + pg_advisory_lock), observability shape.
   Re-read it before starting.

6. Migrations: Alembic only; pg_advisory_lock guard; runs in a
   separate ECS task in the deploy pipeline, not on container start.
   CLI never migrates ~/.litehorse/ — local layout changes are
   handled via on-first-run code paths (e.g. symlink, idempotent
   mkdir).

7. Curator and GEPA produce PROPOSALS. They never auto-merge.
   Approval is human-gated through the existing skill_proposals
   workflow (admin UI in the webapp, CLI: --approve flag).

8. Anthropic / OpenAI / openai-agents SDK pins are managed in
   Phase 46. Do NOT bump them in earlier phases — keep your phase
   diff scoped.

DELIVERABLES: <copy from your phase section above>.
ACCEPTANCE: <copy from your phase section above>.

CROSS-PHASE GATES (must pass before flipping the PROGRESS.md row):
<copy items 1–11>.

When you are blocked, escalate by appending an "Open question" at the
bottom of your branch's description and stop. Do not silently relax a
constraint.
```

