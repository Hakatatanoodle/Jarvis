-- implements §6.7 Action. Structural only.
CREATE TABLE IF NOT EXISTS action (
    id                    UUID PRIMARY KEY,
    plan_id               UUID NOT NULL REFERENCES plan(id),
    capability_id         TEXT NOT NULL,
    type                  TEXT NOT NULL,
    title                 TEXT NOT NULL,
    parameters            JSONB NOT NULL DEFAULT '{}'::jsonb,
    status                TEXT NOT NULL,
    permission_check_id   UUID NULL,
    retry_count           INT NOT NULL DEFAULT 0,
    result_id             UUID NULL,
    created_at            TIMESTAMPTZ NOT NULL,
    started_at            TIMESTAMPTZ NULL,
    completed_at          TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_action_plan ON action(plan_id);
CREATE INDEX IF NOT EXISTS idx_action_status ON action(status);
