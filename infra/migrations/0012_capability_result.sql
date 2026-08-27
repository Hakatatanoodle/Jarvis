-- implements §6.10 CapabilityResult. Insert-only.
CREATE TABLE IF NOT EXISTS capability_result (
    id              UUID PRIMARY KEY,
    action_id       UUID NOT NULL REFERENCES action(id),
    success         BOOLEAN NOT NULL,
    output          JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_time  TEXT NOT NULL DEFAULT '',
    tokens_used     INT NOT NULL DEFAULT 0,
    credits_used    INT NOT NULL DEFAULT 0,
    error           TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capresult_action ON capability_result(action_id);
