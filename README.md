# lite-horse

OpenAI-only assistant runtime built on the
[OpenAI Agents SDK](https://github.com/openai/openai-agents-python) with
skills, persistent memory, FTS5 recall, iteration-budget pressure,
structured error handling, and an offline self-evolution loop.

Two first-class surfaces:

- **`litehorse` CLI** — interactive REPL with streaming markdown, slash
  commands, tool-call display, session resume, and a scripted subcommand
  tree (`sessions`, `skills`, `cron`, `memory`, `logs`, …). See
  [`docs/CLI.md`](docs/CLI.md).
- **`lite_horse.api`** — Python import for embedding in a webapp. See
  [`docs/EMBEDDING.md`](docs/EMBEDDING.md).

More reading:

- [`docs/EVOLVE.md`](docs/EVOLVE.md) — offline SKILL.md evolution loop.
- [`docs/PROGRESS.md`](docs/PROGRESS.md) — phase status and active plan.

## Install

```bash
uv sync --extra dev
```

State lives in `~/.litehorse/` (override with `LITEHORSE_HOME`). On first
run, `load_config()` writes a default `config.yaml`; copy `.env.example` to
`~/.litehorse/.env` and fill in `OPENAI_API_KEY`.

## Quickstart — CLI

```bash
litehorse                        # interactive REPL
litehorse "write a haiku"        # one-shot; streams to stdout, exits
litehorse --session <key>        # REPL bound to an existing session
echo "hi" | litehorse            # one-shot from piped stdin
```

In the REPL, type `/help` for slash commands, Meta-Enter (Esc-Enter) to
submit, Ctrl-C to cancel the in-flight turn (second press within 2 s
exits), Ctrl-D on an empty prompt to exit.

## Quickstart — embedded

Drive one turn from a webapp request handler:

```python
import asyncio
from lite_horse.api import run_turn
from lite_horse.core.session_key import build_session_key

async def demo() -> None:
    key = build_session_key(platform="web", chat_type="dm", chat_id=42)
    result = await run_turn(session_key=key, user_text="hello")
    print(result.final_output)

asyncio.run(demo())
```

Same-`session_key` calls serialize; distinct keys run in parallel. The
full contract (`run_turn`, `end_session`, `search_sessions`, `shutdown`)
is in [`docs/EMBEDDING.md`](docs/EMBEDDING.md).

## Automation — scripted subcommands

Anywhere you'd drive the runtime from a script or CI:

```bash
litehorse sessions list --json
litehorse sessions search "deploy" -n 10
litehorse skills list
litehorse skills evolve <slug> --days 14
litehorse cron list
litehorse cron scheduler                 # runs the scheduler loop
litehorse memory show
litehorse logs tail -n 100
litehorse doctor                         # env + DB + OpenAI key + MCP
litehorse debug share                    # bundle logs + transcript + config
```

Every structured command honors `--json` and emits one NDJSON record per
line to stdout. Structured stderr logging: `LITEHORSE_STRUCTURED_LOGS=1`.

## Cron worker

Runs in its own process. Reads `~/.litehorse/jobs.json`, fires jobs on
schedule, POSTs the result to the webapp with an HMAC-SHA256 signature:

```bash
litehorse cron scheduler
```

Webhook body + signature format:
[`docs/EMBEDDING.md#webhook-delivery-protocol`](docs/EMBEDDING.md#webhook-delivery-protocol).

## Built-in tools

The agent always ships with `memory`, `session_search`, `skill_manage`,
`skill_view`, and `cron_manage`. Extras are opt-in through `config.yaml`:

```yaml
tools:
  web_search: true     # OpenAI-hosted WebSearchTool (billed per call)
```

External MCP servers attach through `config.mcp_servers`; see
[`docs/EMBEDDING.md#mcp-servers`](docs/EMBEDDING.md#mcp-servers).

## Offline evolve

A separate module proposes skill revisions based on recorded failures:

```bash
python -m lite_horse.evolve <skill-slug>
```

Proposals land under `~/.litehorse/skills/.proposals/` for human approval
— they never auto-merge. Gates, fitness, and the approval workflow are
documented in [`docs/EVOLVE.md`](docs/EVOLVE.md).

## Dev surface

- `uv run pytest -q` — hermetic test suite.
- `uv run ruff check src tests` — lint.
- `uv run mypy src` — strict typing.
