# Locked architecture changes vs v0.4

> Part of v0.5. Schema, type, and protocol shapes that every phase must honor. Load when a phase touches Postgres tables, tool Protocols, or `TenantContext`.

```
                                                ┌─────────────────────────────┐
ECS api task ── per-turn build ─►  AgentFactory │ TenantContext               │
                                                │   user_id, agent_id,        │
                                                │   memory_backend (cloud),   │
                                                │   skill_backend  (cloud),   │
                                                │   cron_backend   (cloud),   │
                                                │   recall_backend (cloud),   │
                                                │   feedback_sink  (cloud),   │
                                                └────────────┬────────────────┘
                                                             │ on RunContextWrapper.context
                                                             ▼
                                              memory_tool / skill_manage /
                                              cron_manage / skill_view /
                                              memory_search (NEW Phase 42) /
                                              session_recall (NEW Phase 43)
                                                             │
                                                             ▼
                                                cloud impls → repos → Postgres
                                                local impls → litehorse_home()/...

litehorse CLI  ── per-REPL build ─►  AgentFactory builds the SAME context with
                                     the *_local backends; everything else is
                                     identical.
```

### Repository layout additions

```
src/lite_horse/
├── agent/
│   ├── backends/                 # NEW
│   │   ├── __init__.py           # TenantContext dataclass + Protocols
│   │   ├── memory.py             # MemoryBackend Protocol
│   │   ├── memory_local.py       # wraps MemoryStore from v0.4
│   │   ├── memory_cloud.py       # wraps MemoryRepo from v0.4
│   │   ├── skill.py              # SkillBackend Protocol
│   │   ├── skill_local.py
│   │   ├── skill_cloud.py
│   │   ├── cron.py               # CronBackend Protocol
│   │   ├── cron_local.py
│   │   ├── cron_cloud.py
│   │   ├── recall.py             # RecallBackend Protocol  (Phase 42–43)
│   │   ├── recall_local.py       # chromadb / sqlite-vss
│   │   ├── recall_cloud.py       # pgvector
│   │   ├── feedback.py           # FeedbackSink Protocol  (Phase 44)
│   │   ├── feedback_local.py     # NDJSON to ~/.litehorse/feedback.log
│   │   └── feedback_cloud.py     # turn_outcomes table
│   ├── curator.py                # NEW (Phase 44)
│   └── outcome_classifier.py     # NEW (Phase 44)
├── repositories/
│   ├── agent_repo.py             # NEW (Phase 41)
│   ├── memory_chunk_repo.py      # NEW (Phase 42)
│   ├── session_summary_repo.py   # NEW (Phase 43)
│   ├── turn_outcome_repo.py      # NEW (Phase 44)
│   └── skill_promotion_repo.py   # NEW (Phase 45)
├── models/
│   ├── agent.py                  # NEW (Phase 41)
│   ├── memory_chunk.py           # NEW (Phase 42)
│   ├── session_summary.py        # NEW (Phase 43)
│   ├── turn_outcome.py           # NEW (Phase 44)
│   └── skill_promotion.py        # NEW (Phase 45)
├── providers/
│   ├── embedding.py              # NEW (Phase 42)
│   ├── embedding_openai.py
│   └── embedding_voyage.py
├── evolve/
│   └── gepa/                     # NEW (Phase 45)
│       ├── eval_set.py           # mine eval set from traces
│       ├── population.py         # variant generation
│       ├── fitness.py            # extends evolve/fitness.py
│       └── runner.py
└── alembic/versions/
    ├── 20260512_0003_phase40_*.py    # tool-backend tables (none — code only)
    ├── 20260519_0004_phase41_agents.py
    ├── 20260526_0005_phase42_pgvector.py
    ├── 20260602_0006_phase43_summaries.py
    ├── 20260609_0007_phase44_curator.py
    ├── 20260616_0008_phase45_promotion.py
    └── 20260623_0009_phase46_hardening.py
```

### Locked data-model deltas

```sql
-- ============ Phase 41: agents ============
CREATE TABLE agents (
  id                 UUID PRIMARY KEY,
  user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  slug               TEXT NOT NULL,
  name               TEXT NOT NULL,
  persona            TEXT NOT NULL DEFAULT '',
  default_model      TEXT,
  permission_mode    TEXT NOT NULL DEFAULT 'auto'
                     CHECK (permission_mode IN ('auto','ask','ro')),
  enabled_tools      JSONB NOT NULL DEFAULT '[]'::jsonb,
  rate_limit_per_min INT,
  cost_budget_usd_micro BIGINT,
  is_default         BOOLEAN NOT NULL DEFAULT FALSE,
  archived_at        TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, slug)
);
CREATE UNIQUE INDEX agents_one_default_per_user
  ON agents (user_id) WHERE is_default;
ALTER TABLE users ADD COLUMN default_agent_id UUID REFERENCES agents(id);

-- Backfill: one default agent per existing user; existing rows attach to it.
ALTER TABLE user_documents ADD COLUMN agent_id UUID REFERENCES agents(id);
ALTER TABLE skills         ADD COLUMN agent_id UUID REFERENCES agents(id);
ALTER TABLE cron_jobs      ADD COLUMN agent_id UUID REFERENCES agents(id);
ALTER TABLE sessions       ADD COLUMN agent_id UUID REFERENCES agents(id);
ALTER TABLE skill_proposals ADD COLUMN agent_id UUID REFERENCES agents(id);
-- (After backfill, set NOT NULL on agent_id where appropriate; user-scope
--  rows in skills / cron_jobs / mcp_servers / commands / instructions take
--  agent_id; official-scope rows leave agent_id NULL.)
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON agents
  USING (user_id::text = current_setting('app.user_id', true));

-- ============ Phase 42: memory_chunks (pgvector) ============
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE memory_chunks (
  id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  agent_id     UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  source_kind  TEXT NOT NULL CHECK (source_kind IN
                 ('memory_md','user_md','session_summary','message','skill_body')),
  source_id    TEXT,
  content      TEXT NOT NULL,
  tsv          tsvector GENERATED ALWAYS AS
                 (to_tsvector('simple', content)) STORED,
  embedding    vector(1536),
  embed_model  TEXT NOT NULL,
  ts           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX memory_chunks_tenant ON memory_chunks (user_id, agent_id, ts DESC);
CREATE INDEX memory_chunks_tsv    ON memory_chunks USING GIN (tsv);
CREATE INDEX memory_chunks_vec    ON memory_chunks
  USING hnsw (embedding vector_cosine_ops);
ALTER TABLE memory_chunks ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON memory_chunks
  USING (user_id::text = current_setting('app.user_id', true));

-- ============ Phase 43: session_summaries ============
CREATE TABLE session_summaries (
  session_id    TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  agent_id      UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  summary       TEXT NOT NULL,
  topic         TEXT,
  generated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  generator     TEXT NOT NULL  -- model id used
);
CREATE INDEX session_summaries_tenant
  ON session_summaries (user_id, agent_id, generated_at DESC);
ALTER TABLE session_summaries ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON session_summaries
  USING (user_id::text = current_setting('app.user_id', true));

-- ============ Phase 44: turn_outcomes ============
CREATE TABLE turn_outcomes (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  agent_id      UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  turn_id       UUID NOT NULL,
  source        TEXT NOT NULL CHECK (source IN
                  ('classifier','user_explicit','regex_marker')),
  rating        SMALLINT NOT NULL CHECK (rating BETWEEN -1 AND 1),
  reason        TEXT,
  ts            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX turn_outcomes_agent_ts
  ON turn_outcomes (user_id, agent_id, ts DESC);
ALTER TABLE turn_outcomes ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON turn_outcomes
  USING (user_id::text = current_setting('app.user_id', true));

ALTER TABLE skills
  ADD COLUMN use_count        INT NOT NULL DEFAULT 0,
  ADD COLUMN success_count    INT NOT NULL DEFAULT 0,
  ADD COLUMN error_count      INT NOT NULL DEFAULT 0,
  ADD COLUMN last_used_at     TIMESTAMPTZ,
  ADD COLUMN curator_state    TEXT NOT NULL DEFAULT 'active'
    CHECK (curator_state IN ('active','stale','archived','pinned'));

-- ============ Phase 45: skill_promotion_candidates ============
CREATE TABLE skill_promotion_candidates (
  id                  UUID PRIMARY KEY,
  source_skill_id     UUID NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  unique_user_count   INT NOT NULL,
  use_count           INT NOT NULL,
  success_rate        REAL NOT NULL,
  generated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  status              TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','promoted','rejected'))
);
-- No RLS: this table is admin-only; admin connections do not set app.user_id.

-- ============ Phase 46: gdpr_delete_requests ============
CREATE TABLE gdpr_delete_requests (
  id           UUID PRIMARY KEY,
  user_id      UUID NOT NULL REFERENCES users(id),
  requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  scheduled_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ,
  archive_s3_key TEXT
);
```

### Locked HTTP-surface deltas

```
# Phase 41: agents
GET    /v1/users/me/agents
POST   /v1/users/me/agents                     {slug,name,persona,...}
GET    /v1/users/me/agents/{agent_id}
PUT    /v1/users/me/agents/{agent_id}
DELETE /v1/users/me/agents/{agent_id}          (soft-delete = archive)
POST   /v1/users/me/agents/{agent_id}:default
# TurnRequest gains optional agent_id: when omitted, user's default is used.

# Phase 42-43: recall (no new endpoints; tools added to bundle)

# Phase 44: feedback
POST   /v1/turns/{turn_id}/feedback            {rating: -1|0|1, reason?}

# Phase 45: skill promotion (admin)
GET    /v1/admin/skill-candidates
POST   /v1/admin/skill-candidates/{id}:promote
POST   /v1/admin/skill-candidates/{id}:reject

# Phase 46: GDPR
POST   /v1/users/me:request-delete             (creates row, schedules at +7 days)
DELETE /v1/users/me:cancel-delete              (only if scheduled_at > now)
```

