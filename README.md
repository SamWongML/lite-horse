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
