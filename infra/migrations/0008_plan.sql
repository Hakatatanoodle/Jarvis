-- implements §6.6 Plan. Structural only (docs/ENGINEERING_NOTES.md).
CREATE TABLE IF NOT EXISTS plan (
    id                  UUID PRIMARY KEY,
    decision_id         UUID NOT NULL REFERENCES decision(id),
    title               TEXT NOT NULL,
    objective           TEXT NOT NULL,
    status              TEXT NOT NULL,
    action_ids          JSONB NOT NULL DEFAULT '[]'::jsonb,
    estimated_duration  TEXT NOT NULL DEFAULT '',
    progress            INT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL,
    version             INT NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_plan_decision ON plan(decision_id);
