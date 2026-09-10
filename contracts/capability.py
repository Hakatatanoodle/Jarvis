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

from contracts.enums import CapabilityType, ParamType, RiskLevel


class ParamSpec(BaseModel):
    """Generic-parameter mechanism (2026-09-07). Declares one optional/
    required field a capability accepts beyond its hardcoded shape
    (goal_ref, path_ref, title/start/end, etc. — those stay exactly as
    they are; this is deliberately additive, not a replacement).

    Why this exists: before this, every new capability field (M5's
    title/start/end/account_ref, M6's path_ref/content) needed its own
    hand-written prompt text, JSON schema entry, `_validate()` branch,
    and dispatch.py clarify-gate — fine for two milestones' worth of
    fields, not fine for "Google Calendar has ~20 features." A
    capability now declares its extra fields data-only
    (`Capability.extra_parameters: dict[str, ParamSpec]`), and
    capability_invocation/extraction.py + dispatch.py read that
    declaration generically instead of hardcoding a new branch per
    field. `calendar.create_event`'s `recurrence` is the first field
    built this way — see capabilities/primitives/calendar_ops.py.

    Deliberately NOT applied to the existing hardcoded fields
    (goal_ref/path_ref/content/title/start/end/account_ref) in this
    pass — migrating those is a separate, larger, higher-risk change
    the person explicitly deferred; this mechanism is additive
    (new fields only) alongside the old hardcoded ones (strangler
    pattern), not a replacement of them yet."""
    type: ParamType
    required: bool = False
    description: str = ""
    choices: Optional[list[str]] = None  # only meaningful for ParamType.ENUM

    @model_validator(mode="after")
    def enum_must_have_choices(self) -> "ParamSpec":
        if self.type == ParamType.ENUM and not self.choices:
            raise ValueError("ParamType.ENUM requires a non-empty `choices` list.")
        return self


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
    # V1-M6: unconditional override of the dynamic risk computation —
    # when true, check_permission() always returns CONFIRMATION_REQUIRED
    # for this capability regardless of computed_risk (never GRANTED,
    # never DENIED). Added for fs.write: real filesystem writes on the
    # person's actual computer warrant a stricter floor than the
    # baseline_risk_level/ActionType/supports_undo formula was designed
    # around (see M6_HANDOVER_PROMPT.md's settled requirement #3).
    # Defaults false — every existing capability's behavior is
    # unchanged. Additive field, not a redesign of risk_calculator.py.
    force_confirmation: bool = False
    # Generic-parameter mechanism (2026-09-07, additive) — see
    # ParamSpec's docstring above. Empty dict (the default) means "no
    # generic extra fields," i.e. every existing capability's behavior
    # is byte-for-byte unchanged.
    extra_parameters: dict[str, ParamSpec] = Field(default_factory=dict)
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
