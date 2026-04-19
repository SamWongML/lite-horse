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
