-- Fix (2026-08-07, ARCHITECTURE_ISSUES.md): Mission uses a fresh `id`
-- per version by design (§6.1) — correct for Mission's own version
-- history. But Goal.mission_id referenced that same per-version id
-- directly, so every Mission edit silently orphaned every existing Goal
-- from Planning/Reflection retrieval (they filter goals by the
-- *currently active* mission's id).
--
-- Fix: a stable identity that survives every version. One row per
-- mission lineage, never updated. Mission.identity_id and
-- Goal.mission_id both point at this instead of a specific version row.

CREATE TABLE IF NOT EXISTS mission_identity (
    id UUID PRIMARY KEY
);

ALTER TABLE mission ADD COLUMN IF NOT EXISTS identity_id UUID;

-- Backfill: one identity per version-chain, rooted at whichever version
-- has previous_version_id IS NULL (the original v1 of each lineage).
INSERT INTO mission_identity (id)
SELECT id FROM mission WHERE previous_version_id IS NULL
ON CONFLICT DO NOTHING;

WITH RECURSIVE lineage AS (
    SELECT id, id AS root_id FROM mission WHERE previous_version_id IS NULL
    UNION ALL
    SELECT m.id, l.root_id
    FROM mission m
    JOIN lineage l ON m.previous_version_id = l.id
)
UPDATE mission SET identity_id = lineage.root_id
FROM lineage
WHERE mission.id = lineage.id;

ALTER TABLE mission ALTER COLUMN identity_id SET NOT NULL;
ALTER TABLE mission ADD CONSTRAINT mission_identity_id_fkey
    FOREIGN KEY (identity_id) REFERENCES mission_identity(id);
CREATE INDEX IF NOT EXISTS idx_mission_identity ON mission(identity_id);

-- Repoint every existing Goal at the stable identity instead of the
-- specific version row it happened to be created under.
ALTER TABLE goal DROP CONSTRAINT IF EXISTS goal_mission_id_fkey;

UPDATE goal SET mission_id = mission.identity_id
FROM mission
WHERE goal.mission_id = mission.id;

ALTER TABLE goal ADD CONSTRAINT goal_mission_id_fkey
    FOREIGN KEY (mission_id) REFERENCES mission_identity(id);
