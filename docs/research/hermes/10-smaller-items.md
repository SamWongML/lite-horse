# §10. Smaller but worth-noting

> Mix of in-scope and deferred items. See [README.md](README.md).

- **No prompt caching strategy beyond `prompt_cache_retention="24h"`**.
  Anthropic prompt caching is content-addressed; for the same user
  hitting the same agent across turns, the cache can be much hotter if
  the system prompt is segmented (static skills index → per-session
  memory → per-turn input) so only the tail invalidates.
- **`anthropic >= 0.30, < 1.0`** in `pyproject.toml` is far behind the
  current SDK (sub-1.0 versions are 2024-vintage). Anthropic prompt
  caching, batch API, and computer-use require newer SDK + Sonnet 4.6 /
  Opus 4.7 model IDs that aren't pinned anywhere.
- **`openai-agents >= 0.14.1, < 0.15`** is similarly tight; the SDK has
  shipped multiple breaking-change minors since.
- **No model-id constants** — `gpt-5.4`, `claude-opus`, `claude-sonnet`
  are scattered as strings. Hermes routes through provider profiles for
  this.
- **`api.py` is "deprecated for cloud, kept for dev REPL"** but
  `web/turn_engine.py` still imports private internals from it
  (`_ensure_ready`, `_process_stream_event`, `_StreamCounters`). That
  back-channel needs cleanup before `api.py` can actually be removed.
- **No `requirements.txt` for `infra/`** committed (only
  `infra/requirements.txt` for CDK-app deps). The Lambda-runtime case
  doesn't apply here; this is fine.
- **No HSTS / CSP headers on FastAPI**; ALB will terminate TLS but app-
  level security headers are a defense-in-depth gap.
- **Audit log is append-only in DB but archives to S3 — verify the
  shipper exists**. The bucket is provisioned with versioning + Glacier
  lifecycle; I didn't find a writer path that uploads `audit_log` rows
  to the bucket.
- **Documents folder mentions "v0.4 multi-tenant cloud rollout" in
  PROGRESS.md** — read that for the in-flight phase plan; the
  conclusions here are independent of any phase still in progress.

