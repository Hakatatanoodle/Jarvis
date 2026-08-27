"""implements §6.7 Action

`permission_check_id` was added in the contract-review round specifically
to close the audit gap: an Action with no traceable authorization is
unauditable (violates PS-05). §6.7's JSON shape shows it as a plain
required `id`, with no null option.

ARCHITECTURE ISSUE (logged in ARCHITECTURE_ISSUES.md, 2026-08-01):
taken completely literally, a required non-null permission_check_id would
make it impossible to construct an Action in its own `Pending` first
lifecycle state, since by definition the Permission Check hasn't run yet
at that point (§2's pipeline runs Permission Check *after* Planner, and
§6.9 says the Action Engine "may not transition the Action past `Ready`"
without a resolved confirmation — implying `Ready` is reached only once a
PermissionCheckResult exists, not before). The most faithful
interpretation that keeps both the lifecycle and the audit requirement
intact: permission_check_id is Optional at construction (nullable only
while status == Pending), and becomes mandatory the moment status
advances past Pending. Enforced below via a model_validator.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from contracts.enums import ActionStatus, ActionType


class Action(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    plan_id: str
    capability_id: str
    type: ActionType
    title: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: ActionStatus = ActionStatus.PENDING
    permission_check_id: Optional[str] = None
    retry_count: int = Field(default=0, ge=0)
    result_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @model_validator(mode="after")
    def permission_check_required_past_pending(self) -> "Action":
        if self.status != ActionStatus.PENDING and self.permission_check_id is None:
            raise ValueError(
                f"Action.permission_check_id is required once status leaves "
                f"Pending (current status: {self.status}). See ARCHITECTURE_ISSUES.md "
                f"2026-08-01 entry for why this is nullable only pre-Ready."
            )
        return self
