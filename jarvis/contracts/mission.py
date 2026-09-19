"""implements §6.1 Mission

One active record, versioned, never overwritten. Owned by the Constitution
system (mission/); loaded at startup and editable only through an explicit,
rare user action (§5.1).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from contracts.enums import MissionStatus


class Mission(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    # Fix (2026-08-07, ARCHITECTURE_ISSUES.md): `id` is deliberately new
    # per version (§6.1) — correct for Mission's own version history, but
    # anything that needs to reference "the Mission" stably across edits
    # (Goal.mission_id) must NOT use `id` for that, or every Mission edit
    # orphans every existing reference. `identity_id` stays constant
    # across every version of the same mission: set to the first
    # version's own id at creation, then carried forward unchanged on
    # every supersede (mission/api.py's set_mission()).
    identity_id: str = Field(default_factory=lambda: str(uuid4()))
    version: int = Field(default=1, ge=1)
    title: str
    statement: str
    principles: list[str] = Field(default_factory=list)
    status: MissionStatus = MissionStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    previous_version_id: Optional[str] = None

    @field_validator("statement")
    @classmethod
    def statement_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Mission.statement cannot be empty (§6.1 validation rule)")
        return v

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Mission.title cannot be empty")
        return v

    # NOTE — cross-record invariant, not enforceable on a single instance:
    # "exactly one active Mission may exist at any time" (§6.1). This is
    # enforced by mission/api.py (M1) at write time, not by this model.
