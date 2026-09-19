"""implements V1-M1 Conversation layer §6/8 (V1_M1_IMPLEMENTATION_PLAN.md)

Restores the `Conversation` stage named in HOW_JARVIS_THINKS.md's
original flow but never built in V0. ConversationTurn is the flat,
append-only "Archive" concept named at MVP-scoping stage
(Jarvis_Architecture_Review_v1.md line 110: "Memory (Archive = flat
append-only log; Memory = rule-based creation only...)") but never
actually built as its own contract in V0 — only Memory/Goal's own
ARCHIVED lifecycle *status* exists, which is a different thing.

Deliberately minimal: no lifecycle, no update path, no status field.
One row per turn, both routing paths (conversation and reasoning).
Owned entirely by conversation/ (one writer per entity) — never read by
Reasoning or anything else in M1, so it can't become a second, competing
source of truth about the user. Raw material for a future Learning
Engine (still scaffolded, still deferred), not consumed by anything yet.

`session_id` added when M1's session-context follow-up landed
(2026-08-09): one id per CLI process, generated once at import time in
conversation/api.py. This is what makes "gone after /exit" a real
boundary instead of an assumption — without it, reopening the terminal
later would silently pull in a prior run's turns as if they were still
"recent," which would be real cross-session memory (M2/M3 territory)
wearing M1's clothes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    user_text: str
    response_text: str
    routed_to: Literal["conversation", "reasoning"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
