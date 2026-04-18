# hermes-lite

Single-user, OpenAI-only personal assistant built on top of the
[OpenAI Agents SDK](https://github.com/openai/openai-agents-python). Keeps the
behaviors that made `NousResearch/hermes-agent` interesting — skills with
progressive disclosure, persistent memory, FTS5 recall, iteration-budget
pressure, and a Telegram gateway — and throws away everything else.

See `docs/IMPLEMENTATION_PLAN.md` for the engineering plan.

## Quick start

```bash
uv sync --extra dev
uv run hermeslite --help
```

State lives in `~/.hermeslite/` (override with `HERMESLITE_HOME`). On first run
`load_config()` writes a default `config.yaml`; copy `.env.example` to
`~/.hermeslite/.env` and fill in `OPENAI_API_KEY`.

## Attribution

The FTS5 session store and MEMORY/USER file formats are conceptually derived
from `NousResearch/hermes-agent` (MIT). The SKILL.md progressive-disclosure
spec is from `agentskills.io`.
