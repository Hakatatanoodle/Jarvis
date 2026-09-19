-- implements §6.3 Memory
-- Structural only, per docs/ENGINEERING_NOTES.md (2026-08-02): PK/FK/
-- NOT NULL/types. Business rules (valid type/status/importance values,
-- confidence bounds, "at least one source_id") live in contracts/memory.py
-- and memory/api.py, not here.
CREATE TABLE IF NOT EXISTS memory (
    id                  UUID PRIMARY KEY,
    type                TEXT NOT NULL,
    title               TEXT NOT NULL,
    value               TEXT NOT NULL,
    confidence          DOUBLE PRECISION NOT NULL,
    importance          TEXT NOT NULL,
    status              TEXT NOT NULL,
    source_ids          JSONB NOT NULL DEFAULT '[]'::jsonb,
    related_goal_ids    JSONB NOT NULL DEFAULT '[]'::jsonb,
    related_memory_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL,
    version             INT NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_memory_status ON memory(status);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory(type);
