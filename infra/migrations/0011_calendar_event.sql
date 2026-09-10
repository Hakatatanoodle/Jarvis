-- Local calendar store backing calendar.read_events / calendar.create_event
-- primitives (CAP-01/CAP-02: capability abstracts the provider; a real
-- Google/Outlook plugin can replace this later without touching callers).
CREATE TABLE IF NOT EXISTS calendar_event (
    id          UUID PRIMARY KEY,
    title       TEXT NOT NULL,
    start_at    TIMESTAMPTZ NOT NULL,
    end_at      TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calendar_event_start ON calendar_event(start_at);
