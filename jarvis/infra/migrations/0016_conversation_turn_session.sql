-- M1 session-context follow-up (2026-08-09): adds the real session
-- boundary that makes "gone after /exit" an enforced fact, not an
-- assumption. Nullable + no backfill on purpose — pre-existing rows
-- from before this migration have no real session to attribute them to,
-- and leaving them NULL means they naturally never match any new
-- session_id filter, which is exactly the right behavior (they're from
-- a session that's already over).

ALTER TABLE conversation_turn ADD COLUMN IF NOT EXISTS session_id UUID;
CREATE INDEX IF NOT EXISTS idx_conversation_turn_session ON conversation_turn(session_id, created_at);
