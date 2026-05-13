# Phase 46 — Hardening: GDPR delete, audit shipper, SDK bumps, CLI parity gate (+300 / +400 LOC)

> Part of v0.5. See [README.md](README.md) for objective/non-goals, [_contract.md](_contract.md) for binding rules, [_architecture.md](_architecture.md) for shared types, [_briefing.md](_briefing.md) for the subagent briefing template.

**Objective.** Close the operational gaps the v0.5 surface introduces;
bump SDKs to current; lock the CLI parity invariant in CI.

**Deliverables.**

- Alembic `0009_phase46_hardening.py`: `gdpr_delete_requests` table.
- `web/routes/users.py` adds:
  - `POST /v1/users/me:request-delete` — creates a row,
    `scheduled_at = now() + interval '7 days'`, returns confirmation.
  - `DELETE /v1/users/me:cancel-delete` — cancels if before
    `scheduled_at`.
- Worker gains `gdpr_delete` handler (daily tick scans for due rows):
  - Exports the user's data to a versioned S3 prefix under
    `LITEHORSE_S3_BUCKET_AUDIT_ARCHIVE`.
  - Inside one transaction: deletes from every tenant-scoped table
    (cascades handle most; cron, MCP, embeddings need explicit
    sweeps), then anonymises `audit_log.actor_id` to a tombstone
    value (audit history retained, identity scrubbed).
  - Sets `gdpr_delete_requests.completed_at`,
    `archive_s3_key`.
- Audit-archive shipper: a worker tick uploads `audit_log` rows older
  than 90 days into the same bucket as Parquet, then `DELETE`s them
  from PG. Bucket already has versioning + Glacier lifecycle from v0.4.
- `web/middleware/security_headers.py` — adds HSTS, CSP
  (`default-src 'none'`; tightened by route), X-Frame-Options,
  Referrer-Policy. Wired into `create_app`. Disabled when
  `LITEHORSE_ENV=local` for CLI-served `/debug/*`.
- SDK bumps in `pyproject.toml`:
  - `anthropic >= 0.65, < 1.0` (prompt caching params, batch).
  - `openai >= 2.5, < 3`.
  - `openai-agents >= 0.16, < 0.18`.
  - Smoke-test fixtures updated; any breakages fixed in this phase
    (do not roll forward).
- `lite_horse/constants/models.py` (NEW): canonical model-id
  constants (`MODEL_GPT_5_4`, `MODEL_CLAUDE_OPUS_4_7`, etc.).
  Replace string literals across `agent/factory.py`,
  `cron/delivery.py`, tests.
- Anthropic prompt-caching strategy: split system prompt into three
  cache-stable layers — (1) bundled instructions + skills index
  (cached 24 h), (2) per-agent persona + memory.md + user.md (cached
  per turn), (3) per-turn input. Implemented in
  `agent/instructions.py::make_instructions_for_user` via the SDK's
  `CacheControl` parameter.
- `tests/lint/test_cli_parity.py` is upgraded to a hard gate: every
  cross-phase CLI parity gate from Phases 40–45 is wired into pytest
  and runs in CI (fixture-only — no LLM calls needed for the parity
  shape; LLM-touching tests stay marked `slow` and skipped by default).
- README + `docs/HTTP-API.md` updated with v0.5 surface deltas.
- `docs/research/hermes/` re-scored: items closed by v0.5 are
  ticked, items deferred to v0.6 are listed under a new "Deferred to
  v0.6" section at the foot.
- `docs/PROGRESS.md` v0.5 row 46 flipped to ✅; v0.5 plan flipped to
  **SHIPPED**.

**Acceptance.**

- GDPR delete end-to-end: a request scheduled +7 days, fast-forwarded
  via test clock, results in zero rows under that `user_id` across
  every tenant-scoped table; `archive_s3_key` populated.
- Audit shipper round-trip: a row aged 91 days lives in S3 and is
  gone from PG.
- HSTS + CSP headers present on production responses; not present in
  `LITEHORSE_ENV=local`.
- Anthropic cache-hit ratio ≥80% across the second turn of a
  steady-state session in a load test.
- All Phases-40–45 CLI parity gates green in CI.
- Cross-phase gates 1–8 pass.

