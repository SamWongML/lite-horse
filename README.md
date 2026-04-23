# lite-horse

Embeddable OpenAI-only assistant runtime built on the
[OpenAI Agents SDK](https://github.com/openai/openai-agents-python).

Skills with progressive disclosure, persistent memory, FTS5 recall,
iteration-budget pressure, structured error handling, and an offline
self-evolution loop — all consumed as a Python package by a single
project-management webapp. No standalone CLI, no chat-platform adapters.

- [`docs/EMBEDDING.md`](docs/EMBEDDING.md) — integration contract (env,
  `config.yaml`, MCP, cron webhook spec).
- [`docs/EVOLVE.md`](docs/EVOLVE.md) — offline SKILL.md evolution loop.
- [`docs/PROGRESS.md`](docs/PROGRESS.md) — phase status and active plan.

## Install

```bash
uv sync --extra dev
```

State lives in `~/.litehorse/` (override with `LITEHORSE_HOME`). On first
run, `load_config()` writes a default `config.yaml`; copy `.env.example` to
`~/.litehorse/.env` and fill in `OPENAI_API_KEY`.

## Quickstart

Drive one turn from the webapp:

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

Same-`session_key` calls serialize; distinct keys run in parallel. See
[`docs/EMBEDDING.md`](docs/EMBEDDING.md) for the full surface
(`run_turn`, `end_session`, `search_sessions`, `shutdown`).

## Cron worker

Runs in its own process. Reads `~/.litehorse/jobs.json`, fires jobs on
schedule, POSTs the result to the webapp with an HMAC-SHA256 signature:

```bash
uv run python -c \
  "from lite_horse.cron.scheduler import run_scheduler_blocking; \
   run_scheduler_blocking()"
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

- `uv run pytest -q` — 245+ tests, hermetic.
- `uv run ruff check src tests` — lint.
- `uv run mypy src` — strict typing.
- `uv run litehorse-debug` — local debug REPL. Not a product surface.
