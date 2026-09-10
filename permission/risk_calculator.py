"""
Dynamic risk calculator (decision #2, §4; §6.9). Resolves the M0 deferral
logged in ARCHITECTURE_ISSUES.md: computed_risk is now actually derived,
not just schema-validated.

M4 default heuristics are intentionally simple (consistent with the same
"simplest correct first" principle the architect gave for M3) since real
per-capability risk knowledge only exists once real capabilities are
registered (M5): action type drives impact, capability.supports_undo
drives reversibility, scope/parameter_risk default to their lowest tier
until there's real signal to assess them from.
"""
from __future__ import annotations

from contracts.action import Action
from contracts.capability import Capability
from contracts.enums import ActionType, RiskLevel
from contracts.permission_check_result import RiskFactors

_ORDINAL = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}
_FROM_ORDINAL = {v: k for k, v in _ORDINAL.items()}


def compute_risk(capability: Capability, action: Action) -> tuple[RiskLevel, RiskFactors]:
    impact_bump = 1 if action.type in (ActionType.WRITE, ActionType.COMMUNICATE) else 0
    reversibility_bump = 0 if capability.supports_undo else 1
    scope_bump = 0  # M4 default — no bulk-action detection yet
    parameter_bump = 0  # M4 default — needs real per-capability rules (M5)

    total = _ORDINAL[capability.baseline_risk_level] + impact_bump + reversibility_bump + scope_bump + parameter_bump
    total = max(0, min(3, total))
    computed = _FROM_ORDINAL[total]

    factors = RiskFactors(
        parameter_risk="not assessed (M4 default, capability-specific rules land in M5)",
        impact=f"{action.type.value} action: {'+1' if impact_bump else 'no bump'}",
        reversibility=f"supports_undo={capability.supports_undo}: {'no bump' if capability.supports_undo else '+1'}",
        scope="single-target (M4 default, no bulk-action detection yet)",
    )
    return computed, factors
