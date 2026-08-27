"""implements §6.6 Plan

References exactly one Decision, owns one or more Actions. Completed Plans
are immutable — that invariant depends on `status`, which is exactly the
kind of state-dependent rule a single pydantic model can't self-enforce
at construction time (a Plan can legitimately be constructed in any status
across its lifecycle). Real enforcement lives in planner/api.py (M4): any
update() call on a Plan whose current status is Completed must be rejected
there, not here.
"""
from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from contracts.enums import PlanStatus


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    decision_id: str
    title: str
    objective: str
    status: PlanStatus = PlanStatus.DRAFT
    action_ids: list[str] = Field(default_factory=list)
    estimated_duration: str = ""
    progress: int = Field(default=0, ge=0, le=100)
    version: int = Field(default=1, ge=1)

    @field_validator("action_ids")
    @classmethod
    def at_least_one_action(cls, v: list[str]) -> list[str]:
        if len(v) < 1:
            raise ValueError("Plan must contain at least one Action (§6.6 invariant)")
        return v
