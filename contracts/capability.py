"""implements §6.8 Capability

`baseline_risk_level` was renamed from `risk_level` (decision #2, §4) to
be honest that it's one input to the dynamic risk formula, not the
verdict itself. `capability_type` (decision #5) exists specifically so
composite capabilities can never quietly become a second orchestrator:
a composite capability compiles to a Plan via `compiles_to_plan_template`
and the Planner expands it — the Capability layer never executes it
directly. That rule is encoded below as a hard model-level invariant,
not just a docstring promise.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from contracts.enums import CapabilityType, RiskLevel


class Capability(BaseModel):
    id: str  # e.g. "calendar.create_event" — human-readable, stable, not a UUID
    name: str
    description: str
    category: str
    capability_type: CapabilityType
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    baseline_risk_level: RiskLevel
    required_permissions: list[str] = Field(default_factory=list)
    supports_undo: bool = False
    enabled: bool = True
    version: int = Field(default=1, ge=1)
    compiles_to_plan_template: Optional[str] = None

    @model_validator(mode="after")
    def composite_must_compile_to_plan(self) -> "Capability":
        if self.capability_type == CapabilityType.COMPOSITE and not self.compiles_to_plan_template:
            raise ValueError(
                "Composite capabilities must declare compiles_to_plan_template "
                "(decision #5, §4) — a composite capability that calls other "
                "capabilities directly is a second orchestrator and is not allowed."
            )
        if self.capability_type == CapabilityType.PRIMITIVE and self.compiles_to_plan_template:
            raise ValueError(
                "Primitive capabilities cannot declare compiles_to_plan_template "
                "(only composite capabilities compile to a Plan)."
            )
        return self
