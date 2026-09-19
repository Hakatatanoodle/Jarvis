-- implements §6.1 Mission
CREATE TABLE IF NOT EXISTS mission (
    id                   UUID PRIMARY KEY,
    version              INT NOT NULL CHECK (version >= 1),
    title                TEXT NOT NULL CHECK (btrim(title) <> ''),
    statement            TEXT NOT NULL CHECK (btrim(statement) <> ''),
    principles           JSONB NOT NULL DEFAULT '[]'::jsonb,
    status               TEXT NOT NULL CHECK (status IN ('active', 'archived')),
    created_at           TIMESTAMPTZ NOT NULL,
    updated_at           TIMESTAMPTZ NOT NULL,
    previous_version_id  UUID NULL REFERENCES mission(id)
);

-- §6.1 invariant: "exactly one active Mission may exist at any time."
-- A unique index on a constant expression, filtered to active rows, means
-- Postgres itself rejects a second active row — enforced at the database,
-- not just in application code.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_mission
    ON mission ((1))
    WHERE status = 'active';
