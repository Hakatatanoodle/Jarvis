"""implements §6.11 LearningProposal

A candidate pattern, never a write (decision #10, §4 — Learning Engine
may only propose; Memory System is the only writer). `evidence_ids`
requires a minimum of 2 — no single-event learning (§5.10 / LE-02).

ARCHITECTURE ISSUE (logged in ARCHITECTURE_ISSUES.md, 2026-08-01): §6.11
explicitly says "Full shape available on request from the architect if
not reconstructable from this summary." I reconstructed the fields below
from the summary itself plus learning.md's LE-05 ("Log includes: Evidence,
Confidence, Date learned, Date updated, Current status") and LE-06 (user
rejection lowers confidence and records the correction). This is a
best-faith reconstruction, not a confirmed contract — flagging for
architect sign-off before M8 (Learning Engine) is built against it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from contracts.enums import LearningCategory, LearningStatus


class LearningProposal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    category: LearningCategory
    title: str  # short human-readable summary, e.g. "User prefers morning coding"
    description: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    status: LearningStatus = LearningStatus.PROPOSED
    resulting_memory_id: Optional[str] = None  # set only by Memory System, on acceptance
    rejection_reason: Optional[str] = None  # set when user rejects (LE-06)
    proposed_value: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None

    @field_validator("evidence_ids")
    @classmethod
    def minimum_two_evidence_items(cls, v: list[str]) -> list[str]:
        if len(v) < 2:
            raise ValueError(
                "LearningProposal requires at least 2 evidence_ids — "
                "no single-event learning (§5.10 / learning.md LE-02)."
            )
        return v
