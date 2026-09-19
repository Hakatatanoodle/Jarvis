-- Append-only audit trail for Goal changes.
--
-- ARCHITECTURE ISSUE (see ARCHITECTURE_ISSUES.md, M1 entry): §6.2's Goal
-- schema has a plain `version` integer with no previous_version_id or
-- snapshot mechanism (unlike Mission, §6.1, which explicitly gets a new
-- id per version). Because Goal ids are referenced pervasively elsewhere
-- (parent_goal_id, dependencies arrays, Decision.goal_ids) a
-- Mission-style "new id per version" would break every existing
-- reference on every edit. This table is the resolution: Goal keeps a
-- stable id and in-place `version` counter; every meaningful update is
-- additionally snapshotted here before the update is applied, satisfying
-- Goals.md G-03 ("Never overwrite. Archive previous versions. Store why
-- they changed.") without breaking referential stability.
CREATE TABLE IF NOT EXISTS goal_history (
    id          UUID PRIMARY KEY,
    goal_id     UUID NOT NULL REFERENCES goal(id),
    version     INT NOT NULL,
    snapshot    JSONB NOT NULL,
    reason      TEXT NULL,
    changed_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_goal_history_goal ON goal_history(goal_id);
