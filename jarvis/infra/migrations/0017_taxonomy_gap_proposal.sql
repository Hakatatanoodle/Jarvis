-- V1-M3 follow-up (2026-08-15b): a taxonomy-gap candidate — memory-
-- worthy content that doesn't fit Preference/Fact/Skill/Relationship/
-- Constraint — used to only get an ephemeral log.info() line, which
-- means the gap effectively disappears once logs rotate. Architect
-- review flagged this: fine to defer building an actual review/
-- proposal-approval workflow for M3, not fine for the gap to vanish
-- with no trail at all. This table is the trail — durable, queryable,
-- reviewed manually for now (see memory/api.py's record_taxonomy_gap /
-- list_taxonomy_gaps and cli.py's /memory gaps). No status/workflow
-- columns yet on purpose — this is a record, not a queue; promoting a
-- proposal to a real taxonomy category is still a human/architect call.
CREATE TABLE IF NOT EXISTS taxonomy_gap_proposal (
    id               UUID PRIMARY KEY,
    title            TEXT NOT NULL,
    value            TEXT NOT NULL,
    raw_user_text    TEXT NOT NULL,
    confidence       DOUBLE PRECISION NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_taxonomy_gap_proposal_created_at ON taxonomy_gap_proposal(created_at);
