# §7. Agent-loop and tool-use gaps

> Not in v0.5 scope (deferred to v0.6+). See [README.md](README.md).

### 7.1 No explicit planner

Both Hermes and lite-horse rely on the SDK's native function-calling +
tool descriptions; neither has an explicit ReAct/Plan-and-Execute step.
Hermes compensates with rich procedural memory (skills); lite-horse has
the same mechanism but a smaller skill library.

For complex multi-step tasks ("plan a vacation, book the flights, add
to calendar"), an explicit planner pass that decomposes the user
request before tool selection would help. Optional.

### 7.2 No subagent / handoff pattern

The OpenAI Agents SDK supports agent handoffs natively (one agent
delegating to a specialist agent). lite-horse uses side-agents
(`Consolidator`, `EvolutionHook`'s distiller/refiner) but never user-
facing handoffs.

For an "agent management center" where the user might say "let me talk
to the coding agent now," handoff is the right abstraction. Missing
today.

### 7.3 Tool surface is small

Always-on tools: `memory`, `session_search`, `skill_manage`, `skill_view`,
`cron_manage`. Conditional: `WebSearchTool`, MCP, GitHub. Missing for
"personal assistant":

- file/document attachment ingestion (read PDFs / images the user
  uploads via the website)
- email send/draft (with user-scoped OAuth)
- calendar read/write (Google / Microsoft)
- code execution sandbox (Hermes has 7 backends; lite-horse has none)

The S3 `LITEHORSE_S3_BUCKET_ATTACHMENTS` bucket is provisioned but the
ingestion tool isn't built.

### 7.4 No streaming-tool-output to the user

SSE streams `StreamDelta` (text), `StreamToolCall`, `StreamToolOutput`.
Tool outputs reach the user as the agent finishes them, but tools that
themselves stream (web search progress, code-exec stdout) don't get
forward-streamed. Probably fine for v1.

