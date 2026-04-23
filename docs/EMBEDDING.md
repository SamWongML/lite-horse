# Embedding `lite-horse`

`lite-horse` is an embeddable Python package. The consuming webapp imports
`lite_horse.api`, calls `run_turn()` on each user message, and lets the cron
worker deliver scheduled output back via a signed webhook. There is **no**
standalone CLI, server, or chat-platform adapter.

This document is the integration contract: env vars, `config.yaml` shape,
MCP-server expectations, cron-delivery protocol. The surface is versioned by
the package version; anything outside of `lite_horse.api` is internal and may
change without notice.

---

## Processes

Two processes run side-by-side. Both consume the same `~/.litehorse/` state
dir (override with `LITEHORSE_HOME`).

| Process | Entry point | What it does |
|---|---|---|
| **Webapp** (your code) | `lite_horse.api.run_turn(...)` | Handles user messages. One process per host. |
| **Cron worker** | `lite_horse.cron.scheduler.run_scheduler_blocking()` | Fires scheduled jobs, POSTs results to the webapp. |

The webapp is expected to supervise the cron worker (systemd, docker,
supervisord — your call). Neither process reads stdin; both log to stderr.

---

## Environment variables

All are read from the process environment; `lite-horse` will also load
`~/.litehorse/.env` on startup if it exists (via `python-dotenv`, no
override).

| Name | Required | Who reads it | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | yes | webapp + cron | Passed through to the OpenAI SDK. |
| `LITEHORSE_HOME` | no | both | Override for `~/.litehorse`. |
| `LITEHORSE_WEBHOOK_SECRET` | yes (for webhook delivery) | cron + webapp | HMAC secret; webapp MUST verify every incoming delivery. |

Missing `OPENAI_API_KEY` surfaces as an OpenAI SDK error on the first
`run_turn`. Missing `LITEHORSE_WEBHOOK_SECRET` in the cron process causes
webhook delivery to log an error and silently drop the message — by design:
we refuse to POST unsigned content to the webapp.

---

## `config.yaml`

Lives at `~/.litehorse/config.yaml`. Created with defaults on the first
`load_config()` call. All keys are optional; omitting a block falls back to
the defaults below.

```yaml
model: gpt-5.4                    # any OpenAI chat model id
model_settings:
  reasoning_effort: medium        # none | low | medium | high
  parallel_tool_calls: true
agent:
  max_turns: 90                   # hard cap per run_turn
memory:
  enabled: true
  user_profile_enabled: true
tools:
  web_search: false               # OpenAI-hosted WebSearchTool (billed)
sandbox:
  enabled: false                  # hosted CodeInterpreter
mcp_servers: []                   # see below
```

The `gateway.telegram.*` block is legacy from v0.1 and is ignored by the
embedded runtime. It's still accepted in the schema so older configs parse
cleanly.

---

## MCP servers

External MCP servers are declared in `config.yaml` and attached once on the
first `run_turn`:

```yaml
mcp_servers:
  - name: rag-broker
    url: http://localhost:7444/mcp
    cache_tools_list: true
```

- `url` must be `http://` or `https://`. Anything else is rejected by the
  pydantic validator.
- Connection happens inside `_ensure_ready()` under a lock — repeated
  `run_turn` calls share the same connection.
- A failing `connect()` is logged and skipped; the rest of the agent still
  runs. Do **not** rely on an MCP server being present for correctness.
- MCP URLs must never come from user input. Treat them as trusted
  configuration.

---

## Public API

```python
from lite_horse.api import (
    run_turn, end_session, search_sessions, shutdown, RunResult, SearchHit,
)
from lite_horse.core.session_key import build_session_key
```

### `run_turn`

```python
async def run_turn(
    *,
    session_key: str,          # stable key — build with build_session_key
    user_text: str,
    source: str = "web",       # free-form origin tag; used by search
    user_id: str | None = None,
    max_turns: int | None = None,
) -> RunResult
```

- Same `session_key` calls serialize on a per-key `asyncio.Lock`; distinct
  keys run in parallel.
- `RunResult` is `{final_output, session_key, turn_count, tool_calls}`.
- Retries `RATE_LIMIT` and `NETWORK` failures (1s, 4s). All other
  `ErrorKind`s raise through.

### `build_session_key`

```python
build_session_key(
    platform="web",            # free-form; "web", "slack", etc.
    chat_type="dm",            # "dm" | "channel" | "thread"
    chat_id=42,
    thread_id=None,            # optional suffix for forum threads
)
# -> "agent:main:web:dm:42"
```

Use this for every call — don't roll your own string. The scheduler derives
the same shape for cron-originated runs.

### `end_session` / `search_sessions`

- `end_session(session_key)` — stamp `ended_at` + `end_reason`. Call this
  when the user leaves the chat surface; it's cheap but not required for
  correctness.
- `search_sessions(query, *, limit=20, source=None)` — FTS5 lookup across all
  persisted messages. Returns `SearchHit` rows (id, session_id, role,
  timestamp, snippet, source). Raises if called before the first `run_turn`.

### `shutdown`

```python
await shutdown()
```

Closes connected MCP servers. Optional; intended for graceful-exit paths
(SIGTERM handlers, test teardown). The process can exit without it.

---

## Cron worker

### Running it

```bash
uv run python -c \
  "from lite_horse.cron.scheduler import run_scheduler_blocking; \
   run_scheduler_blocking()"
```

Blocks forever. One process per host. Reads `~/.litehorse/jobs.json` (the
same file the agent's `cron_manage` tool mutates) and fires APScheduler
triggers from `schedule` strings (5-field crontab or `@minutely`/`@hourly`
/`@daily`/`@weekly`).

### Job shape

```json
{
  "id": "a1b2c3d4e5f6",
  "schedule": "0 9 * * *",
  "prompt": "Summarize yesterday's Slack threads and DM me the highlights.",
  "delivery": {"platform": "webhook", "url": "https://app.example.com/lh/cron"},
  "enabled": true,
  "disabled_reason": null
}
```

`delivery.platform` is either `"log"` (dev-only, prints to stderr) or
`"webhook"` — the only production-supported target.

Three consecutive `MODEL_REFUSAL` firings auto-disable the job with
`disabled_reason="model_refusal_strikeout"`. The webapp can surface disabled
jobs for operator review.

### Webhook delivery protocol

For each firing the cron worker runs the prompt through `run_turn`, then
POSTs the result to `delivery.url`.

**Request**

```
POST <delivery.url>
Content-Type: application/json
X-LiteHorse-Signature: sha256=<hex hmac of body>

{"text": "<final_output>", "session_key": "<session_key>"}
```

- Body is compact JSON (`ensure_ascii=false`). Sign the **raw body bytes**
  exactly as received.
- Signature: `HMAC-SHA256(LITEHORSE_WEBHOOK_SECRET, body)`, hex-encoded,
  prefixed with `sha256=`.

**Verification (webapp side)**

```python
import hmac, hashlib, os

def verify(body: bytes, header: str) -> bool:
    want = "sha256=" + hmac.new(
        os.environ["LITEHORSE_WEBHOOK_SECRET"].encode(),
        body, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(want, header)
```

Reject anything that doesn't match. Constant-time compare is mandatory.

**Retry policy**

- 2xx: success.
- 5xx or transport error: retry up to 3 times with `1s / 4s / 16s` backoff.
- 4xx: give up — the webapp rejected the shape, replay won't help.

Webhook deliveries are **at-least-once**. The webapp must dedupe or tolerate
replays; we don't stamp a unique id on the body (by design — job + firing
time are enough for a deduping consumer, and the signature pins the payload).

---

## State on disk

Everything under `~/.litehorse/` is the canonical state. Back up this dir;
there is no secondary store.

```
~/.litehorse/
├── config.yaml                 # see above
├── .env                        # optional; populated by the operator
├── sessions.db                 # SQLite + FTS5 — all conversations
├── MEMORY.md                   # durable agent-managed facts
├── USER.md                     # user profile
├── jobs.json                   # cron jobs (cron_manage + scheduler)
└── skills/
    ├── <name>/SKILL.md         # one per skill, progressive-disclosure markdown
    ├── <name>/.stats.json      # usage + error counters (Phase 20)
    └── .proposals/<name>/<ts>.{md,json}   # Phase 24 evolve proposals
```

Skills live on disk so the agent (and the evolve pipeline) can mutate them
without redeploying. Bundled skills under `src/lite_horse/skills/bundled/`
are copied into `~/.litehorse/skills/` on first run and then left alone.

---

## Invariants the webapp must preserve

1. **One `lite-horse` process per host.** The `SessionDB` and `Agent` are
   process-wide singletons. Two processes pointing at the same
   `LITEHORSE_HOME` will corrupt the FTS index.
2. **Never import `lite_horse.evolve` from the request path.** It's
   admin-only offline tooling and pulls in extra heavy surface. Asserted by
   `tests/test_evolve_e2e.py::test_api_does_not_transitively_import_evolve`.
3. **Verify every webhook signature.** Unsigned or mis-signed deliveries
   must be dropped.
4. **Treat MCP server URLs as configuration**, not user input. Writing them
   from a user message is out of scope.
5. **Don't bypass `build_session_key`.** String-format drift breaks the
   per-key lock.
