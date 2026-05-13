# §9. Surface gaps for "agent management center" use case

> Drives v0.5 Phase 41 (per-agent personas + agent CRUD). See [README.md](README.md).

The brief specifically mentions a Claude-managed-agents-style website. To
serve that surface, lite-horse needs:

- **Multiple agents per user** (today: one). See §5.3.
- **Agent CRUD endpoints** — `POST /v1/agents`, `GET /v1/agents/{id}`,
  `DELETE /v1/agents/{id}`. The `default_agent_id` slot on the user.
- **Per-agent tool / MCP / skill bundles** — each agent picks from the
  user's pool. The `EffectiveConfig` resolver needs an `agent_id` axis.
- **Public sharing / publishing of agents** (optional but expected).
- **Conversation history grouped by agent** (today: by `session_key`,
  but an agent should own its sessions).
- **Run lifecycle UI** — abort/resume/replay are partially there
  (`POST /v1/turns/{turn_id}:abort`); replay isn't.
- **WebSocket alternative to SSE** for browser UIs that need
  bidirectional cancel signals. SSE works but the abort path goes via
  a separate POST and races.

