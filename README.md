# lite-horse

> Multi-tenant cloud assistant service with persistent memory, layered
> conditional skills, Postgres-backed cross-session recall, KMS-encrypted
> BYO provider keys, and an offline self-evolution worker. Built on the
> [OpenAI Agents SDK](https://github.com/openai/openai-agents-python).

![python](https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white)
![agents-sdk](https://img.shields.io/badge/openai--agents-0.14-412991?logo=openai&logoColor=white)
![lint](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)
![typing](https://img.shields.io/badge/typing-mypy%20strict-2a6db2)

| Surface | Use it when | Docs |
|---|---|---|
| **HTTP API** (FastAPI on AWS ECS) | The product surface for any webapp | [docs/HTTP-API.md](docs/HTTP-API.md) |
| **`litehorse` CLI** | Local development against `~/.litehorse/`; not deployed in v0.4 | [docs/CLI.md](docs/CLI.md) |
| **`lite_horse.api` Python** | Legacy embedded path, kept for the dev REPL only | [docs/EMBEDDING.md](docs/EMBEDDING.md) (deprecated) |

## Highlights

- **Multi-tenant by design** — every Postgres table carries `user_id` with RLS; Redis keys, KMS encryption contexts, and the MCP connection pool all pivot on the same boundary.
- **Layered config** — `bundled` → `official` → `user` precedence for skills, instructions, slash commands, MCP servers, and cron jobs. Admins push official content with mandatory / opt-out semantics.
- **Persistent memory** — `MEMORY.md` + `USER.md` per user with compression-as-consolidation and periodic nudge.
- **Conditional skills** — markdown procedures the agent loads only when their triggers match; offline evolve worker proposes revisions from recorded failures (human-approved merge only).
- **Cross-session recall** — Postgres tsvector FTS over messages with a `session_search` tool.
- **Streaming HTTP API** — SSE turn endpoint with `Idempotency-Key` replay, ask-mode permission round-trip, and per-session distributed locks.
- **Multi-provider + cost meter** — OpenAI / Anthropic via a unified `ModelProvider`; per-turn `usage_events` rows in micro-USD; per-user daily cost budget enforced in Redis.
- **Per-tenant rate limit** — Redis token bucket on `POST /v1/turns*` (60/min default).
- **Scheduler + worker split** — APScheduler ticks every 60 s; standalone SQS worker runs turns + signed HMAC-SHA256 webhook delivery and the daily evolve queue.
- **Observability** — structlog JSON logs, OTel traces, EMF metrics; CloudWatch dashboard + alarms.

## Install

```bash
uv sync --extra dev
cp .env.example .env                # fill in DB / Redis / JWKS / provider keys
```

Requires Python 3.11+.

## Quickstart

### Cloud HTTP API

```bash
docker compose up                  # api + scheduler + worker + postgres + redis
curl -sS -X POST http://localhost:8080/v1/turns \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"session_key":"demo","text":"hello"}'
```

Surface reference: [docs/HTTP-API.md](docs/HTTP-API.md). Live OpenAPI
schema at `GET /openapi.json` on a running instance.

### CLI (developer use only)

```bash
litehorse                          # interactive REPL against ~/.litehorse/
litehorse "write a haiku"          # one-shot; stream, then exit
litehorse --session <key>          # resume an existing session
echo "hi" | litehorse              # one-shot from piped stdin
```

The CLI is **not deployed** in v0.4. It's a local-only convenience for
working on skills/memory before pushing them to the cloud.

### Embedded (deprecated)

The Python `lite_horse.api` import path is preserved so the dev REPL
keeps building, but webapps should integrate via the HTTP API. See
[docs/EMBEDDING.md](docs/EMBEDDING.md).

## Automation

Every scripted subcommand honors `--json` and emits one NDJSON record per line.

```bash
litehorse sessions list --json
litehorse sessions search "deploy" -n 10
litehorse skills list
litehorse skills evolve <slug> --days 14
litehorse cron list
litehorse cron scheduler                 # starts the scheduler process
litehorse memory show
litehorse logs tail -n 100
litehorse doctor                         # env + DB + OpenAI key + MCP
litehorse debug share                    # bundle logs + transcript + config
```

Opt-in structured stderr logs: `LITEHORSE_STRUCTURED_LOGS=1`.

## Scheduler + worker

Two standalone processes share the api's container image:

```bash
python -m lite_horse.scheduler         # 60 s cron tick + daily evolve tick
python -m lite_horse.worker            # SQS long-poll: turns + webhooks + evolve
```

Webhook deliveries are HMAC-SHA256 signed; the secret resolves via
Secrets Manager in cloud envs (5-min TTL cache). Local dev uses an
in-memory queue.

## Tools

Always on: `memory`, `session_search`, `skill_manage`, `skill_view`,
`cron_manage`. Extras via `config.yaml`:

```yaml
tools:
  web_search: true          # OpenAI-hosted WebSearchTool (billed per call)
mcp_servers:                # external MCP servers — see docs/EMBEDDING.md#mcp-servers
  - ...
```

## Offline evolve

A separate module proposes skill revisions from recorded failures:

```bash
python -m lite_horse.evolve <skill-slug>
```

Proposals land under `~/.litehorse/skills/.proposals/` for human approval —
they never auto-merge. Gates, fitness, and the approval workflow:
[docs/EVOLVE.md](docs/EVOLVE.md).

## Docs

| File | What |
|---|---|
| [docs/HTTP-API.md](docs/HTTP-API.md) | Cloud surface contract (auth, rate limits, idempotency, errors) |
| [docs/CLI.md](docs/CLI.md) | Developer CLI reference (dev-only; not deployed) |
| [docs/SECRET_ROTATION.md](docs/SECRET_ROTATION.md) | Secrets Manager rotation runbook |
| [docs/EVOLVE.md](docs/EVOLVE.md) | Offline SKILL.md evolution loop |
| [docs/EMBEDDING.md](docs/EMBEDDING.md) | Legacy embedded Python surface (deprecated) |
| [docs/PROGRESS.md](docs/PROGRESS.md) | Phase status and active plan |

## Development

```bash
uv run pytest -q                   # hermetic test suite
uv run ruff check src tests        # lint
uv run mypy src                    # strict typing
```
