"""implements §6.3 Memory

Created only by the Memory System (decision #10, §4 — single-writer rule).
Learning Engine, Reflection, and Insight Engine may only *propose* via
LearningProposal (§6.11); they never call a memory-create path directly.
That rule is a process/API-boundary invariant (enforced in memory/api.py,
the single write path) and cannot be expressed on the model itself, but is
documented here so it stays visible next to the schema it protects.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from contracts.enums import Importance, MemoryStatus, MemoryType


class Memory(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    type: MemoryType
    title: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    importance: Importance = Importance.MEDIUM
    status: MemoryStatus = MemoryStatus.ACTIVE
    source_ids: list[str] = Field(default_factory=list)
    related_goal_ids: list[str] = Field(default_factory=list)
    related_memory_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)

    @field_validator("source_ids")
    @classmethod
    def at_least_one_source(cls, v: list[str]) -> list[str]:
        if len(v) < 1:
            raise ValueError(
                "Memory requires at least one source_id (§6.3 invariant: "
                "'At least one source_id required')"
            )
        return v
