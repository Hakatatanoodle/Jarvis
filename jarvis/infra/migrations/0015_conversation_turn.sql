-- V1-M1 Conversation layer (V1_M1_IMPLEMENTATION_PLAN.md §5/6).
-- Flat, append-only archive of every conversational turn — both routing
-- paths (conversation and reasoning). No lifecycle, no update path, by
-- design: matches the original "Archive = flat append-only log" concept
-- (Jarvis_Architecture_Review_v1.md), which existed at MVP-scoping stage
-- but was never actually built as its own table in V0.

CREATE TABLE IF NOT EXISTS conversation_turn (
    id UUID PRIMARY KEY,
    user_text TEXT NOT NULL,
    response_text TEXT NOT NULL,
    routed_to TEXT NOT NULL CHECK (routed_to IN ('conversation', 'reasoning')),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_turn_created_at ON conversation_turn(created_at DESC);
