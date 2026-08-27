-- Follow-up to 0001-0003, per direct architect guidance (2026-08-02):
-- "Keep database logic inside the storage/repository layer as the
-- project grows. Don't let business logic slowly leak into SQL files."
--
-- 0001-0003 put several business-rule invariants directly into SQL as a
-- defense-in-depth measure: enum-value CHECKs (status/type/priority),
-- non-empty-string CHECKs, the goal_not_own_parent CHECK, and a partial
-- unique index enforcing "exactly one active Mission." All of these
-- duplicate checks that already exist in contracts/*.py (pydantic
-- validators) and goals/api.py's own logic — mission/api.py and
-- goals/api.py are the only writers to these tables (RFC-014's interface
-- boundary), so a second, SQL-level copy of the same rule is exactly the
-- kind of two-sources-of-truth drift that RFC-015 already suffered from.
--
-- This migration drops every constraint that encodes a *business* rule,
-- keeping only what's genuinely structural: primary keys, foreign keys
-- (referential integrity IS a database concern), and NOT NULL. The one
-- invariant that needed real replacement rather than just deletion —
-- "exactly one active Mission," which is concurrency-sensitive — is now
-- enforced via a Postgres advisory lock taken inside set_mission()'s
-- transaction (see mission/api.py), so the guarantee still holds under
-- concurrent writers without living in a SQL constraint.

ALTER TABLE mission DROP CONSTRAINT IF EXISTS mission_statement_check;
ALTER TABLE mission DROP CONSTRAINT IF EXISTS mission_status_check;
ALTER TABLE mission DROP CONSTRAINT IF EXISTS mission_title_check;
ALTER TABLE mission DROP CONSTRAINT IF EXISTS mission_version_check;
DROP INDEX IF EXISTS one_active_mission;

ALTER TABLE goal DROP CONSTRAINT IF EXISTS goal_not_own_parent;
ALTER TABLE goal DROP CONSTRAINT IF EXISTS goal_priority_check;
ALTER TABLE goal DROP CONSTRAINT IF EXISTS goal_status_check;
ALTER TABLE goal DROP CONSTRAINT IF EXISTS goal_title_check;
ALTER TABLE goal DROP CONSTRAINT IF EXISTS goal_type_check;
ALTER TABLE goal DROP CONSTRAINT IF EXISTS goal_version_check;

-- What's left after this migration: PRIMARY KEY, FOREIGN KEY, NOT NULL,
-- and column types. That's the line going forward — see
-- docs/ENGINEERING_NOTES.md.
