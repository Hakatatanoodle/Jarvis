"""implements §6.9 PermissionCheckResult

The authoritative risk-and-authorization record (decision #2, §4). Did
not exist before the contract review; it's load-bearing — the Action
Engine may not transition an Action past `Ready` until
`confirmation_status == confirmed` when `permission_status ==
confirmation_required`.

ARCHITECTURE ISSUE (logged in ARCHITECTURE_ISSUES.md, 2026-08-01):
§6.9's invariant states "computed_risk is always derived from
baseline_risk + risk_factors — never set directly by any caller." The
*mechanism* that performs that derivation (the dynamic risk formula:
baseline_risk + parameter_risk + impact + reversibility + scope) is
explicitly scoped to permission/risk_calculator.py, which is an M4
deliverable, not M0. This contract enforces everything that's checkable
at the schema level today (valid enum values, permission/confirmation
status consistency) but cannot yet reject "a caller set computed_risk to
something the formula wouldn't have produced," because the formula
doesn't exist yet. Full enforcement of this invariant lands in M4 when
the only legitimate constructor becomes the risk calculator's factory
function.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from contracts.enums import ConfirmationStatus, PermissionStatus, RiskLevel


class RiskFactors(BaseModel):
    """The four inputs (beyond baseline_risk) that feed the dynamic risk
    formula (decision #2, §4): baseline_risk + parameter_risk + impact +
    reversibility + scope. Kept as free-text strings per §6.9's literal
    JSON shape — the risk calculator (M4) interprets these, this contract
    just carries them.
    """

    parameter_risk: str = ""
    impact: str = ""
    reversibility: str = ""
    scope: str = ""


class PermissionCheckResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    capability_id: str
    plan_id: Optional[str] = None
    proposed_parameters: dict = Field(default_factory=dict)
    baseline_risk: RiskLevel
    computed_risk: RiskLevel
    risk_factors: RiskFactors = Field(default_factory=RiskFactors)
    permission_status: PermissionStatus
    confirmation_status: ConfirmationStatus = ConfirmationStatus.NOT_APPLICABLE
    permission_rule_used: str
    action_id: Optional[str] = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None

    @model_validator(mode="after")
    def confirmation_status_consistent_with_permission_status(self) -> "PermissionCheckResult":
        if (
            self.permission_status == PermissionStatus.CONFIRMATION_REQUIRED
            and self.confirmation_status == ConfirmationStatus.NOT_APPLICABLE
        ):
            raise ValueError(
                "permission_status='confirmation_required' requires a real "
                "confirmation_status (pending/confirmed/rejected), not "
                "'not_applicable' (§6.9 invariant)."
            )
        return self
