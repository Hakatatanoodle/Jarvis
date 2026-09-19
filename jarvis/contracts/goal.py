"""implements §6.2 Goal

Single-parent tree + dependency array (ADR-018, decision #4 in the
implementation prompt). This deliberately supersedes the older Goals.md
(G-02), which allowed true multi-parent graphs — that simplification is
locked and is not to be reverted (§0 Prime Directive).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from contracts.enums import GoalStatus, GoalType, Priority


class Goal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    type: GoalType
    title: str
    description: str = ""
    status: GoalStatus = GoalStatus.DRAFT
    priority: Priority = Priority.MEDIUM
    parent_goal_id: Optional[str] = None
    mission_id: str
    dependencies: list[str] = Field(default_factory=list)
    success_metrics: list[str] = Field(default_factory=list)
    deadline: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def no_self_reference(self) -> "Goal":
        if self.parent_goal_id is not None and self.parent_goal_id == self.id:
            raise ValueError("Goal cannot be its own parent (§6.2 invariant)")
        if self.id in self.dependencies:
            raise ValueError("Goal cannot depend on itself (§6.2 invariant)")
        return self

    # NOTE — cross-record invariants, not enforceable on a single instance:
    # "no dependency cycles" and "single parent only" across the whole tree
    # require graph traversal over multiple Goal records. These are
    # enforced by goals/api.py (M1), which is the only writer of Goal
    # records, per §7's interface-boundary rule (RFC-014).
