-- implements §6.9 PermissionCheckResult. Structural only. Insert-only
-- (like decision) except resolved_at, set once when confirmation resolves.
CREATE TABLE IF NOT EXISTS permission_check_result (
    id                      UUID PRIMARY KEY,
    capability_id           TEXT NOT NULL,
    plan_id                 UUID NULL REFERENCES plan(id),
    proposed_parameters     JSONB NOT NULL DEFAULT '{}'::jsonb,
    baseline_risk           TEXT NOT NULL,
    computed_risk           TEXT NOT NULL,
    risk_factors            JSONB NOT NULL DEFAULT '{}'::jsonb,
    permission_status       TEXT NOT NULL,
    confirmation_status     TEXT NOT NULL,
    permission_rule_used    TEXT NOT NULL,
    action_id               UUID NULL REFERENCES action(id),
    decided_at              TIMESTAMPTZ NOT NULL,
    resolved_at             TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_pcr_action ON permission_check_result(action_id);
