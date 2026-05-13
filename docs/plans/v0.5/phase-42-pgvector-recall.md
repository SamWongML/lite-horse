# Phase 42 — pgvector recall + `memory_search` tool (+700 / +500 LOC)

> Part of v0.5. See [README.md](README.md) for objective/non-goals, [_contract.md](_contract.md) for binding rules, [_architecture.md](_architecture.md) for shared types, [_briefing.md](_briefing.md) for the subagent briefing template.

**Objective.** Lift the 2 400-char wholesale-injection ceiling. Memory
becomes searchable across the user's entire agent history, not just
what fits in the current system prompt.

**Deliverables.**

- Alembic `0005_phase42_pgvector.py`: `CREATE EXTENSION IF NOT EXISTS
  vector;` then `memory_chunks` per the data-model delta. HNSW index
  on cosine.
- `models/memory_chunk.py`, `repositories/memory_chunk_repo.py` with
  `upsert_chunk()`, `hybrid_search(query, *, k=5,
  alpha=0.5) -> list[Chunk]` (alpha = blend between BM25 score and
  cosine similarity, configurable).
- `providers/embedding.py` Protocol; `embedding_openai.py`
  (`text-embedding-3-small`, 1536-dim) and `embedding_voyage.py`
  (`voyage-3`, 1024-dim with PCA pad to 1536). Default driven by
  `LITEHORSE_EMBEDDING_PROVIDER`; BYO key honored via `ByoKeyStore`.
- `agent/backends/recall.py` — `RecallBackend` Protocol:
  `index(source_kind, source_id, content)`,
  `search(query, k=5) -> list[Chunk]`, `delete(source_kind, source_id)`.
- `recall_cloud.py`: pgvector via `MemoryChunkRepo`.
- `recall_local.py`: `chromadb.PersistentClient(path=
  litehorse_home() / "embeddings")`. Embedding model is the same
  Protocol; CLI defaults to OpenAI but supports a "no-embed" mode that
  falls back to BM25 only.
- New always-on tool `memory_search(query: str, k: int = 5)` in the
  agent's tool bundle (`agent/factory.py`). Returns top-K chunks with
  `source_kind` + truncated content, formatted as JSON. Description
  emphasises "use this when you need to recall something not visible
  in MEMORY.md".
- Indexing triggers (cloud + local, identical):
  - `MemoryBackend.add` / `replace` → re-chunk and embed the affected
    document (whole-doc re-embed; bounded at 2 400 chars).
  - `SkillBackend.create` / `patch` → embed the body once.
  - Phase 43 will add session-summary indexing.
- Chunking: `tiktoken`-based fixed-window (256 tokens, 32-token
  overlap). One chunk = one row.
- Embedding writes are **best-effort and never block the turn**: cloud
  path enqueues an embed task to SQS; local path embeds inline (CLI
  user accepts the latency). On failure, the row is inserted with
  `embedding=NULL` and a worker will retry.
- Worker gains an `embed` message handler analogous to existing
  `evolve` and webhook handlers.
- Cost model: embedding cost rolls into `usage_events` with
  `model='text-embedding-3-small'` (or chosen). `cost_budget` already
  enforces.

**Acceptance.**

- A user writes `memory(add, "I prefer pnpm")`, then 50 turns later
  asks "what's my package manager preference?" — the agent uses
  `memory_search` to retrieve and answers correctly. Covered by
  `tests/agent/test_recall_e2e.py` against docker-compose.
- Hybrid search: a query containing the exact word "pnpm" ranks the
  pnpm chunk first via BM25 boost; a paraphrase ("which JS package
  installer do I use") still surfaces it via cosine.
- pgvector HNSW index used by EXPLAIN on `hybrid_search`.
- **CLI parity gate.** `litehorse "remember I deploy to fly.io"`,
  then `litehorse "where do I deploy?"` recalls "fly.io" via
  `memory_search`; `~/.litehorse/embeddings/` contains a chromadb
  store with one row.
- Cost: a 1 000-turn fixture session indexes for <$0.05 with
  `text-embedding-3-small`.
- Cross-phase gates 1–8 pass.
- `docs/PROGRESS.md` v0.5 row 42 flipped to ✅.

