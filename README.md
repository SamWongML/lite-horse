# lite-horse

Single-user, OpenAI-only personal assistant built on top of the
[OpenAI Agents SDK](https://github.com/openai/openai-agents-python). Skills with
progressive disclosure, persistent memory, FTS5 recall, iteration-budget
pressure, and a Telegram gateway — nothing else.

See `docs/IMPLEMENTATION_PLAN.md` for the engineering plan.

## Quick start

```bash
uv sync --extra dev
uv run litehorse --help
```

State lives in `~/.litehorse/` (override with `LITEHORSE_HOME`). On first run
`load_config()` writes a default `config.yaml`; copy `.env.example` to
`~/.litehorse/.env` and fill in `OPENAI_API_KEY`.

## Running

```bash
# Interactive chat (fresh session each invocation)
uv run litehorse chat

# Resume a prior session
uv run litehorse chat --session-id cli-abc123

# Telegram gateway (needs TELEGRAM_BOT_TOKEN and allowlist in config.yaml)
uv run litehorse gateway

# APScheduler cron worker (reads ~/.litehorse/jobs.json)
uv run litehorse cron
```

Gateway and cron MUST run as separate processes — cron uses `loop.run_forever()`,
gateway uses signal-driven shutdown.

## systemd deployment

User-mode unit files live in `deploy/`. Install per user:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/gateway.service deploy/cron.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gateway.service cron.service
```

Both units read `~/.litehorse/.env` for `OPENAI_API_KEY` / `TELEGRAM_BOT_TOKEN`.
Adjust `ExecStart` if `litehorse` is not at `/usr/local/bin/litehorse`.

## Built-in tools

The agent always ships with `memory`, `session_search`, and `skill_manage`. Extra
tools are opt-in through `config.yaml`:

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

Never accept MCP server URLs from user messages — keep them in config or code.
