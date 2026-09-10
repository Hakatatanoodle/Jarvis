-- Same pattern as goal_history (see 0003's docstring and
-- ARCHITECTURE_ISSUES.md): §6.3 gives Memory a plain `version` integer
-- with no previous_version_id or snapshot mechanism, same gap Goal had.
-- Applying the same resolution here for consistency: stable id, in-place
-- version counter, append-only history table capturing the pre-change
-- snapshot and why it changed.
CREATE TABLE IF NOT EXISTS memory_history (
    id          UUID PRIMARY KEY,
    memory_id   UUID NOT NULL REFERENCES memory(id),
    version     INT NOT NULL,
    snapshot    JSONB NOT NULL,
    reason      TEXT NULL,
    changed_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_history_memory ON memory_history(memory_id);
