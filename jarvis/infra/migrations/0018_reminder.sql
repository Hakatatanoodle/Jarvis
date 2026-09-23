-- Capabilities V2: reminders. due_at is TIMESTAMPTZ (always UTC-normalized
-- by the executor). Delivery is poll-based: reminders/api.py's
-- pop_due_reminders() atomically flips pending -> delivered, so two
-- overlapping polls can never deliver the same reminder twice.
CREATE TABLE IF NOT EXISTS reminder (
    id           UUID PRIMARY KEY,
    text         TEXT NOT NULL CHECK (btrim(text) <> ''),
    due_at       TIMESTAMPTZ NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'cancelled')),
    created_at   TIMESTAMPTZ NOT NULL,
    delivered_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_reminder_pending_due ON reminder(due_at) WHERE status = 'pending';
