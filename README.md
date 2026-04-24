# lite-horse

> Embeddable OpenAI-only assistant runtime with persistent memory, conditional
> skills, SQLite/FTS5 recall, and an offline self-evolution loop. Built on the
> [OpenAI Agents SDK](https://github.com/openai/openai-agents-python).

![python](https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white)
![agents-sdk](https://img.shields.io/badge/openai--agents-0.14-412991?logo=openai&logoColor=white)
![lint](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)
![typing](https://img.shields.io/badge/typing-mypy%20strict-2a6db2)
![tests](https://img.shields.io/badge/tests-476%20passing-4c1)

Two surfaces over one on-disk state dir (`~/.litehorse/`, overridable via
`LITEHORSE_HOME`):

| Surface | Use it when | Docs |
|---|---|---|
| **`litehorse` CLI** | Interactive REPL or scripted automation/CI | [docs/CLI.md](docs/CLI.md) |
| **`lite_horse.api`** | Importing into a webapp | [docs/EMBEDDING.md](docs/EMBEDDING.md) |

## Highlights

- **Persistent memory** — layered `MEMORY.md` + `USER.md`, compression-as-consolidation, periodic nudge.
- **Conditional skills** — markdown procedures the agent loads only when their triggers match; offline loop proposes revisions from recorded failures (human-approved merge only).
- **Cross-session recall** — SQLite + FTS5 session store with a `session_search` tool.
- **Robust runtime** — structured error classifier, iteration-budget pressure, prompt-cache-aware dynamic instructions.
- **Scheduled work** — APScheduler cron, HMAC-SHA256 signed webhook delivery.
- **Extensible** — external MCP servers via config; opt-in `WebSearchTool`.
- **Interactive CLI** — streaming markdown, slash commands, tool-call approval, session resume, cost meter.

## Install

```bash
uv sync --extra dev
cp .env.example ~/.litehorse/.env   # then fill in OPENAI_API_KEY
```

Requires Python 3.11+. First run writes a default `config.yaml` under
`~/.litehorse/`.

## Quickstart

### CLI

```bash
litehorse                          # interactive REPL
litehorse "write a haiku"          # one-shot; stream, then exit
litehorse --session <key>          # resume an existing session
echo "hi" | litehorse              # one-shot from piped stdin
```

In the REPL: `/help` lists slash commands, **Meta-Enter** submits, **Ctrl-C**
cancels the in-flight turn (press twice within 2 s to exit), **Ctrl-D** on an
empty prompt exits.

### Embedded

```python
import asyncio
from lite_horse.api import run_turn
from lite_horse.core.session_key import build_session_key

async def main() -> None:
    key = build_session_key(platform="web", chat_type="dm", chat_id=42)
    result = await run_turn(session_key=key, user_text="hello")
    print(result.final_output)

asyncio.run(main())
```

Same-`session_key` calls serialize; distinct keys run in parallel. Full
contract (`run_turn`, `run_turn_streaming`, `end_session`, `search_sessions`,
`shutdown`) is in [docs/EMBEDDING.md](docs/EMBEDDING.md).

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

## Cron worker

Runs in its own process, reads `~/.litehorse/jobs.json`, and POSTs each job's
output to your webapp with an HMAC-SHA256 signature:

```bash
litehorse cron scheduler
```

Webhook body + signature format:
[docs/EMBEDDING.md#webhook-delivery-protocol](docs/EMBEDDING.md#webhook-delivery-protocol).

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
| [docs/CLI.md](docs/CLI.md) | CLI reference — keys, slash commands, subcommands |
| [docs/EMBEDDING.md](docs/EMBEDDING.md) | Webapp integration contract |
| [docs/EVOLVE.md](docs/EVOLVE.md) | Offline SKILL.md evolution loop |
| [docs/PROGRESS.md](docs/PROGRESS.md) | Phase status and active plan |

## Development

```bash
uv run pytest -q                   # hermetic test suite
uv run ruff check src tests        # lint
uv run mypy src                    # strict typing
```
