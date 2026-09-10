"""goals.advance — the real implementation of M4's placeholder capability_id.
Touches a Goal via goals/api.py (versioned, audited); Goal has no explicit
progress field in §6.2, so this records a review pass via the existing
history mechanism rather than inventing a new concept."""
from __future__ import annotations

from contracts.capability import Capability
from contracts.enums import CapabilityType, RiskLevel
from capabilities.registry import register
from goals.api import update_goal


async def _advance(parameters: dict) -> dict:
    goal = await update_goal(
        parameters["goal_id"],
        reason=parameters.get("note", "Reviewed via Jarvis planning cycle"),
    )
    return {"goal_id": goal.id, "version": goal.version}


register(
    Capability(
        id="goals.advance", name="Advance Goal", description="Records a review/touch on a Goal.",
        category="Productivity", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["goals.write"], supports_undo=False,
    ),
    _advance,
)
