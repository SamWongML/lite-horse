# §3. CRITICAL BUG — agent tools break multi-tenancy in the cloud path

> Drives v0.5 Phase 40. See [README.md](README.md) for the gap-analysis overview.

> **Severity: blocker for ECS deployment serving multiple users.**

The cloud read path is per-user, but every **write** path the agent itself
exercises is filesystem-backed and global.

### 3.1 Read/write asymmetry

`web/turn_engine.py::run_turn_streaming_for_user` (lines 81-134) correctly
reads `memory.md` / `user.md` from the per-user `MemoryRepo`
(`user_documents` table) and renders them into the system prompt.

But the agent's `memory` tool (`memory/tool.py:31`) does:

```python
store = MemoryStore.for_memory() if target == "memory" else MemoryStore.for_user()
```

…where `MemoryStore.for_memory()` (`memory/store.py:43`) hardcodes:

```python
p = litehorse_home() / "memories" / "MEMORY.md"
```

**Consequence**: when User A's agent calls `memory(action='add', ...)` on
ECS task X, it writes to a single `MEMORY.md` file on that container's
filesystem. User B hitting the same task next sees A's "private" memory
folded into their system prompt. Worse, User A on task Y won't see their
own write. Read injection comes from Postgres; tool writes go to a shared
local file. The two stores diverge instantly under any real traffic.

### 3.2 Same bug, additional surfaces

| Tool / hook | File / line | FS path used | Should write to |
|---|---|---|---|
| `memory_tool` | `memory/tool.py:31` | `litehorse_home()/memories/MEMORY.md` | `MemoryRepo` (per `user_id`) |
| `BudgetHook._consolidate` | `agent/budget.py:158` | same | same |
| `skill_manage` (create/patch) | `skills/manage_tool.py:30` | `skills_root() = litehorse_home()/skills/` | `SkillRepo` (per `user_id`) |
| `EvolutionHook._read_skill_md` | `agent/evolution.py:242` | same | same |
| `cron_manage` | `cron/jobs.py:45` | `litehorse_home()/jobs.json` | `CronRepo` (per `user_id`) |
| Skill activation in prompt | `agent/instructions.py:77` | `litehorse_home()/skills/` | `EffectiveConfig.skills` (already exists, just not wired in) |
| Skill stats (`skill_view` + outcome recording) | `skills/stats.py:56`, `skills/activation.py:192` | `litehorse_home()/...` | `SkillRepo` + per-user counters |

`agent/instructions.py::make_instructions()` (used by the local CLI path)
also reads `MemoryStore.for_memory().render_block()` from disk, but the
cloud path goes through `make_instructions_for_user()` with injected text,
so prompt assembly is fine — only writes are broken.

### 3.3 Why this passed local tests

`docker-compose.yml` runs a single `api` container, so the FS is unified
within one process. The bug only manifests on multi-task ECS, on container
restart (state loss), or when scheduler/worker tasks operate on a different
container's state than the api task that wrote it.

### 3.4 Fix (required before any production deploy)

Replace the FS-backed primitives with thin DB-backed equivalents that
accept `user_id` (already plumbed into `RunContextWrapper` via
`turn_engine.run_turn_streaming_for_user`):

1. Promote `user_id` into `RunContextWrapper.context` (or a dedicated
   `TenantContext`) at agent build time.
2. Rewrite `memory_tool`, `skill_manage`, `cron_manage` to read `user_id`
   from `ctx` and dispatch to `MemoryRepo` / `SkillRepo` / `CronRepo`.
3. Rewrite `BudgetHook._consolidate` and `EvolutionHook._read_skill_md` /
   `_maybe_create_skill` / `_maybe_refine_skill` to take a repo handle
   injected at hook construction (`LiteHorseHooks(...)` already runs
   inside the per-user request scope).
4. Rewire `make_instructions` (CLI path) to dual-mode — keep FS for
   `litehorse` REPL, use repo for cloud.
5. Wire skill activation (`_skills_index` in `instructions.py:77`) to read
   from `EffectiveConfig.skills` (already resolved upstream and available
   on `eff`).
6. Delete `MemoryStore` and `cron/jobs.py` filesystem code paths once the
   migration is done — they're dual-use today and the dual-path is the
   defect.

This is the single largest deliverable on the list. Without it, the rest
of the multi-tenancy work is moot.

