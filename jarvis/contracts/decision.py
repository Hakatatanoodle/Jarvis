"""implements §6.5 Decision

Immutable once created. `estimated_confirmation_needed` is the renamed
field from the old `requires_confirmation` (decision #7, §4): it is a
guess made before parameters are final, never the authoritative answer.
The authoritative answer lives on PermissionCheckResult.confirmation_status
(§6.9), computed later against real parameters.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)  # immutable once created (§6.5)

    id: str = Field(default_factory=lambda: str(uuid4()))
    objective: str
    summary: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    alternatives: list[str] = Field(default_factory=list)
    selected_option: str
    expected_outcome: str
    risks: list[str] = Field(default_factory=list)
    mission_alignment: float = Field(ge=0.0, le=1.0)
    goal_ids: list[str] = Field(default_factory=list)
    context_item_ids: list[str] = Field(default_factory=list)
    requires_plan: bool = False
    estimated_confirmation_needed: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)

    # NOTE (relaxed 2026-08-02, see ARCHITECTURE_ISSUES.md M3 entry): this
    # model originally required len(context_item_ids) >= 1, carried
    # forward from the older RFC-007 doc rather than from the canonical
    # §6.5 in the implementation prompt (which doesn't state this as an
    # invariant). M3's Reasoning Engine surfaced a legitimate case this
    # broke: "no active Goals exist yet" is itself a real, evidence-based
    # Decision with zero ContextItems to point to — fabricating a fake
    # one to satisfy the count would violate ContextItem's own
    # "must reference an existing source" principle even harder.
    # Explainability (DE-08) is still enforced via `reasoning` staying
    # non-empty below.

    @field_validator("reasoning")
    @classmethod
    def reasoning_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "Decision.reasoning cannot be empty — every Decision must be "
                "explainable (DE-08) even when it references zero ContextItems."
            )
        return v
