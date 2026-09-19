-- implements §6.5 Decision
-- Insert-only: Decision is immutable once created (§6.5), so unlike
-- Goal/Memory there's no update path and no history table needed — the
-- row itself IS the permanent record. Structural constraints only
-- (docs/ENGINEERING_NOTES.md): PK/NOT NULL/types.
CREATE TABLE IF NOT EXISTS decision (
    id                          UUID PRIMARY KEY,
    objective                   TEXT NOT NULL,
    summary                     TEXT NOT NULL,
    reasoning                   TEXT NOT NULL,
    confidence                  DOUBLE PRECISION NOT NULL,
    alternatives                JSONB NOT NULL DEFAULT '[]'::jsonb,
    selected_option             TEXT NOT NULL,
    expected_outcome            TEXT NOT NULL,
    risks                       JSONB NOT NULL DEFAULT '[]'::jsonb,
    mission_alignment           DOUBLE PRECISION NOT NULL,
    goal_ids                    JSONB NOT NULL DEFAULT '[]'::jsonb,
    context_item_ids            JSONB NOT NULL DEFAULT '[]'::jsonb,
    requires_plan               BOOLEAN NOT NULL DEFAULT false,
    estimated_confirmation_needed BOOLEAN NOT NULL DEFAULT false,
    -- Not part of §6.5's schema — see ARCHITECTURE_ISSUES.md M3 entry:
    -- the contract has nowhere to persist which intent/retrieval-policy
    -- produced this Decision. Added as a plain extra column rather than
    -- silently dropping the information, since it's clearly useful for
    -- later querying and doesn't change the §6.5 shape callers see.
    intent                      TEXT NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL,
    version                     INT NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_decision_intent ON decision(intent);
CREATE INDEX IF NOT EXISTS idx_decision_created_at ON decision(created_at);
