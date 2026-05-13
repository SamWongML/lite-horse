# §6. Multi-tenancy gaps (beyond the §3 bug)

> Partially closed by v0.5 Phase 46 (GDPR delete). RLS-on-legacy-tables is a Tier-2 follow-up. See [README.md](README.md).

### 6.1 RLS is on, but only on four tables

**Calibrated 2026-05-07.** RLS is real:
`alembic/versions/20260426_0001_initial_schema.py:535` enables RLS and
creates `tenant_isolation` policies on `messages`, `sessions`,
`user_documents`, `skill_proposals`;
`20260430_0002_phase39_user_limits.py:43` adds `FORCE ROW LEVEL
SECURITY` so even table-owners cannot bypass. `db_session()` sets the
`app.user_id` GUC on connection acquire.

The remaining gap is **scope**: `skills`, `cron_jobs`, `commands`,
`instructions`, `mcp_servers`, `usage_events`, and `audit_log` are
WHERE-clause-guarded only. A single missed `where(user_id=...)` on any
of those is a data leak.

**Missing puzzle**: extend RLS to cover every tenant-scoped table.
v0.5 Phase 41 covers this for new tables it adds (`agents`,
`memory_chunks`, `session_summaries`, `turn_outcomes`); a back-fill
migration for the older user-scoped tables is a Tier-2 item.

### 6.2 No per-user MCP server isolation at network layer

`McpPool.acquire(user_id, eff)` returns user-scoped MCP servers, but
they're outbound HTTP — a misconfigured MCP endpoint owned by User A
could be hit by User B if the pool key is wrong. Verify pool keying
includes `user_id` in the cache key, not just the URL.

### 6.3 No per-user PII purge / GDPR delete

There's an `audit_log` and an `opt_out` table, but no documented
"delete everything for user X" operation. For a website hosting personal
assistants, this is required for EU users.

**Missing puzzle**: an admin endpoint + worker job that drops all
`user_id`-scoped rows + S3 prefixes + KMS-encrypted secrets in one
transaction (with audit-archive copy retained).

### 6.4 No per-tenant model usage caps separate from cost

Per-user daily *cost* budget exists. Per-user daily *token* budget,
*request* budget, and *concurrent-turn* budget do not. Cost-only caps
are gameable (a single 1M-token call on a cheap model passes). For a
managed-agents platform, you want layered caps.

### 6.5 No tenant tier model

All users are equal in the schema. A real product distinguishes free /
pro / enterprise with different caps, models, MCP allowances, and rate
limits. The `users` table has `role` (admin/user) but no `plan_tier`.

