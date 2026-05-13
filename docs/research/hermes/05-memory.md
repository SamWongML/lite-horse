# §5. Memory architecture gaps

> Drives v0.5 Phases 42 (pgvector recall) and 43 (session summaries / compaction). See [README.md](README.md).

### 5.1 No semantic / vector retrieval

Memory is flat Markdown injected wholesale into the system prompt, capped
at 2400 chars (`memory.md`) + 1500 chars (`user.md`). No embeddings, no
similarity search, no hybrid retriever.

For a personal-assistant engine that should recall "what did we discuss
about my Q3 strategy three months ago?" this is too tight by ~3 orders of
magnitude. Hermes solves this by deferring to optional providers (mem0 /
supermemory / etc.); lite-horse doesn't even have an interface to plug
one in.

**Missing puzzle**: pgvector extension in RDS + a `memory_chunks` table
keyed on `user_id` with `embedding vector(1536)`, plus a `memory_search`
tool added to the always-on bundle. Embeddings can be Voyage / OpenAI /
Anthropic-Voyage. Cost is small at the embedding layer; the win is large.

Note: Hermes also doesn't have a *native* hybrid retriever — this is a
place lite-horse could be **better than Hermes** with modest effort.

### 5.2 No episodic / session-summary memory

`session_search` does Postgres `tsvector` FTS over messages — keyword
match only. There's no per-session "what happened" summary that survives
into future sessions.

**Missing puzzle**: at session-end (or on idle 24h), generate a 1-3
sentence session summary, store it in a `session_summaries` table. Inject
the most-recent N summaries (or vector-retrieved relevant ones) into the
system prompt. This is the standard ChatGPT-style "memory" pattern.

### 5.3 No `SOUL.md` / persona-as-text

Hermes' `SOUL.md` is the persona/identity layer. lite-horse only has
`memory.md` (agent notes) + `user.md` (about the human). For an "agent
management center" where users may want to spin up *different agents*
with different personas (a shopping assistant vs. a coding buddy), this
slot is missing.

**Missing puzzle**: a per-user-per-agent `agents` table with `persona`
text, default model, default tool bundle, and a `default_agent_id` on
`users`. Today the system has one agent shape per user. This is also
how you map onto the "Claude managed agents" mental model the brief
references.

### 5.4 No memory consolidation across sessions

`Consolidator` runs at WARNING budget tier within a single turn. There's
no equivalent that runs across sessions to compact older `memory.md`
entries when the file approaches its char cap. Once `memory.md` hits 2400
chars, `MemoryFull` raises and writes silently fail.

**Missing puzzle**: a daily per-user job that runs a compaction agent
when memory utilization > 80%, merging similar entries and dropping
stale ones. Same `cron_jobs` infra.

### 5.5 No shared / org memory

If two users at the same company want to share project conventions
("we use pnpm, not npm"), there's no org/team layer. Probably out of
scope for v1, but worth noting as the natural next axis.

