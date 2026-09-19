-- implements §6.2 Goal
CREATE TABLE IF NOT EXISTS goal (
    id              UUID PRIMARY KEY,
    type            TEXT NOT NULL CHECK (type IN ('LifeGoal', 'Project', 'Task', 'Habit')),
    title           TEXT NOT NULL CHECK (btrim(title) <> ''),
    description     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL CHECK (status IN ('Draft', 'Active', 'Paused', 'Completed', 'Archived', 'Cancelled')),
    priority        TEXT NOT NULL CHECK (priority IN ('Low', 'Medium', 'High', 'Critical')),
    parent_goal_id  UUID NULL REFERENCES goal(id),
    mission_id      UUID NOT NULL REFERENCES mission(id),
    dependencies    JSONB NOT NULL DEFAULT '[]'::jsonb,
    success_metrics JSONB NOT NULL DEFAULT '[]'::jsonb,
    deadline        TIMESTAMPTZ NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ NULL,
    version         INT NOT NULL DEFAULT 1 CHECK (version >= 1),

    CONSTRAINT goal_not_own_parent CHECK (parent_goal_id IS NULL OR parent_goal_id <> id)
);

CREATE INDEX IF NOT EXISTS idx_goal_parent ON goal(parent_goal_id);
CREATE INDEX IF NOT EXISTS idx_goal_mission ON goal(mission_id);
CREATE INDEX IF NOT EXISTS idx_goal_status ON goal(status);
