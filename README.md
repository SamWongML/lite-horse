# lite-horse

Embeddable OpenAI-only assistant runtime built on the
[OpenAI Agents SDK](https://github.com/openai/openai-agents-python). Skills with
progressive disclosure, persistent memory, FTS5 recall, iteration-budget
pressure, and an offline self-evolution loop — consumed as a Python package
by a single project-management webapp. No standalone CLI, no chat-platform
adapters.

See [`docs/PROGRESS.md`](docs/PROGRESS.md) for phase status and the active
engineering plan.

## Quick start

```bash
uv sync --extra dev
```

State lives in `~/.litehorse/` (override with `LITEHORSE_HOME`). On first run
`load_config()` writes a default `config.yaml`; copy `.env.example` to
`~/.litehorse/.env` and fill in `OPENAI_API_KEY`.

## Embedding

From Phase 16 onward the webapp imports a single module:

```python
from lite_horse.api import run_turn   # wired in Phase 16
```

Until Phase 16 lands, a minimal debug REPL is available for local development
only:

```bash
uv run litehorse-debug
```

`litehorse-debug` is not a product surface and may change or disappear without
notice.

## Cron worker

```bash
uv run python -c "from lite_horse.cron.scheduler import run_scheduler_blocking; run_scheduler_blocking()"
```

Reads `~/.litehorse/jobs.json`. Delivery is `log` only until Phase 17 adds
webhook delivery to the webapp. The cron worker MUST run in its own process
(the webapp supervises it on boot).

## Built-in tools

The agent always ships with `memory`, `session_search`, `skill_manage`, and
`skill_view`. Extra tools are opt-in through `config.yaml`:

```yaml
tools:
  web_search: true    # OpenAI-hosted WebSearchTool (billed per call)
```

## Attaching an MCP server

For external capabilities (e.g. a RAG broker) mount an MCP server at runtime
instead of adding more builtins. Example using the SDK's streamable-HTTP client:

```python
from agents import Runner
from agents.mcp import MCPServerStreamableHttp

from lite_horse.agent.factory import build_agent

async def run(prompt: str, session) -> str:
    async with MCPServerStreamableHttp(
        name="rag-broker",
        params={"url": "http://localhost:7444/mcp"},
        cache_tools_list=True,
    ) as rag:
        agent = build_agent()
        agent.mcp_servers = [rag]
        result = await Runner.run(agent, prompt, session=session)
        return result.final_output
```

Phase 23 moves this wiring into `config.yaml`. Never accept MCP server URLs
from user messages — keep them in config or code.
