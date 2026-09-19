-- implements §6.12 InsightRecord. Insert-only except user_response.
CREATE TABLE IF NOT EXISTS insight_record (
    id              UUID PRIMARY KEY,
    mode            TEXT NOT NULL,
    scope           TEXT NOT NULL,
    narrative       TEXT NOT NULL,
    evidence        JSONB NOT NULL DEFAULT '[]'::jsonb,
    wins            JSONB NOT NULL DEFAULT '[]'::jsonb,
    problems        JSONB NOT NULL DEFAULT '[]'::jsonb,
    user_response   TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_insight_mode ON insight_record(mode);
CREATE INDEX IF NOT EXISTS idx_insight_created_at ON insight_record(created_at);
