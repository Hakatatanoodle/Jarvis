"""V1-M3: Memory candidate contract.

A MemoryCandidate is never persisted on its own — it's the structured
shape memory/extraction.py proposes from a user utterance, which then
passes through memory/candidate_policy.py's deterministic validation
before memory/api.py (the single writer, decision #10) ever turns it
into a real, persisted `Memory`. Kept as its own contract rather than
reusing `contracts.memory.Memory` directly (V1-M3 input §"M3-A") because
a candidate can be rejected, ambiguous, or missing a taxonomy fit —
states a persisted Memory should never be able to represent.

`type: Optional[MemoryType]` is the mechanism for V1-M3 Decision 1's
taxonomy-gap path: None means the extraction judged the statement
memory-worthy but it doesn't fit Preference/Fact/Skill/Relationship/
Constraint. That candidate is never written; see
memory/candidate_policy.py's TAXONOMY_GAP outcome.

`explicit` exists to make V1-M3 Decision 2 (inference explicitly
deferred) a structural fact instead of a convention: nothing downstream
may create a durable Memory from a candidate where this is False.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from contracts.enums import Importance, MemoryType


class MemoryScope(str, Enum):
    """V1-M3 Decision 6: session context (M1) and durable memory (M3)
    are different scopes, and the system must not silently guess
    between them. Local to this contract rather than contracts/enums.py
    — that file is shared across every persisted §6 contract, and scope
    is a candidate-only concept with no column on the `memory` table."""
    DURABLE = "durable"
    SESSION = "session"
    AMBIGUOUS = "ambiguous"


class Sensitivity(str, Enum):
    """Set by memory/candidate_policy.py's deterministic check ONLY
    (V1-M3 Decision 3: "the validation/policy layer must own this
    decision"). Extraction may not set this field to anything but the
    SAFE default — an LLM's opinion on whether something is a secret is
    not authoritative."""
    SAFE = "safe"
    SENSITIVE = "sensitive"


class MemoryCandidate(BaseModel):
    type: Optional[MemoryType]
    title: str
    value: str
    scope: MemoryScope
    explicit: bool
    importance: Importance = Importance.MEDIUM
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    sensitivity: Sensitivity = Sensitivity.SAFE
    raw_user_text: str
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def taxonomy_fit(self) -> bool:
        return self.type is not None
