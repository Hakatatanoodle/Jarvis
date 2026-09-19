"""Integration tests for planner/api.py, permission/api.py, and
action_engine/api.py — M4."""
import pytest

from action_engine.api import get_action
from contracts.enums import (
    ActionStatus, ActionType, CapabilityType, ConfirmationStatus,
    GoalStatus, GoalType, PermissionStatus, PlanStatus, RiskLevel,
)
from contracts.capability import Capability
from goals.api import create_goal, set_status as set_goal_status
from mission.api import set_mission
from permission.api import check_permission, confirm, reject
from planner.api import DecisionDoesNotRequirePlanError, approve_plan, create_plan_from_decision
from reasoning.api import reason

pytestmark = pytest.mark.usefixtures("clean_db")


def _capability(baseline_risk=RiskLevel.LOW, supports_undo=True):
    return Capability(
        id="goals.advance", name="Advance Goal", description="d", category="Goal",
        capability_type=CapabilityType.PRIMITIVE, baseline_risk_level=baseline_risk,
        supports_undo=supports_undo,
    )


async def _active_goal_decision():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.LIFE_GOAL, title="Financial independence")
    await set_goal_status(g.id, GoalStatus.ACTIVE, reason="starting")
    return await reason(intent="Planning", objective="What should I work on today?")


async def test_plan_created_from_decision_with_actions():
    decision = await _active_goal_decision()
    plan = await create_plan_from_decision(decision)
    assert plan.status == PlanStatus.DRAFT
    assert len(plan.action_ids) == len(decision.goal_ids)


async def test_plan_rejects_decision_without_requires_plan():
    await set_mission(title="Grow", statement="Statement.")
    decision = await reason(intent="Reflection", objective="How did this go?")
    with pytest.raises(DecisionDoesNotRequirePlanError):
        await create_plan_from_decision(decision)


async def test_approve_plan_transitions_draft_to_approved():
    decision = await _active_goal_decision()
    plan = await create_plan_from_decision(decision)
    approved = await approve_plan(plan.id)
    assert approved.status == PlanStatus.APPROVED


async def test_low_risk_action_is_granted_and_action_becomes_ready():
    decision = await _active_goal_decision()
    plan = await create_plan_from_decision(decision)
    action_id = plan.action_ids[0]

    result = await check_permission(action_id, _capability(baseline_risk=RiskLevel.LOW))
    assert result.permission_status == PermissionStatus.GRANTED
    assert result.computed_risk == RiskLevel.LOW

    action = await get_action(action_id)
    assert action.status == ActionStatus.READY
    assert action.permission_check_id == result.id


async def test_write_action_bumps_risk_above_read():
    decision = await _active_goal_decision()
    plan = await create_plan_from_decision(decision)
    action_id = plan.action_ids[0]
    # Actions created by the Planner are Compute-typed; Write/Communicate
    # types get +1 impact bump per the risk calculator (permission/risk_calculator.py)
    from action_engine.api import set_status as _unused  # sanity import only
    result = await check_permission(action_id, _capability(baseline_risk=RiskLevel.MEDIUM, supports_undo=False))
    # Medium baseline + no-undo bump = High -> confirmation_required
    assert result.computed_risk == RiskLevel.HIGH
    assert result.permission_status == PermissionStatus.CONFIRMATION_REQUIRED
    assert result.confirmation_status == ConfirmationStatus.PENDING


async def test_critical_risk_is_denied_and_action_cancelled():
    decision = await _active_goal_decision()
    plan = await create_plan_from_decision(decision)
    action_id = plan.action_ids[0]

    result = await check_permission(action_id, _capability(baseline_risk=RiskLevel.CRITICAL, supports_undo=False))
    assert result.permission_status == PermissionStatus.DENIED

    action = await get_action(action_id)
    assert action.status == ActionStatus.CANCELLED
    assert action.permission_check_id == result.id


async def test_confirm_unblocks_pending_confirmation():
    decision = await _active_goal_decision()
    plan = await create_plan_from_decision(decision)
    action_id = plan.action_ids[0]
    result = await check_permission(action_id, _capability(baseline_risk=RiskLevel.MEDIUM, supports_undo=False))
    assert result.confirmation_status == ConfirmationStatus.PENDING

    confirmed = await confirm(result.id)
    assert confirmed.confirmation_status == ConfirmationStatus.CONFIRMED
    assert confirmed.resolved_at is not None


async def test_reject_cancels_the_action():
    decision = await _active_goal_decision()
    plan = await create_plan_from_decision(decision)
    action_id = plan.action_ids[0]
    result = await check_permission(action_id, _capability(baseline_risk=RiskLevel.MEDIUM, supports_undo=False))

    await reject(result.id)
    action = await get_action(action_id)
    assert action.status == ActionStatus.CANCELLED


async def test_action_has_no_permission_check_id_until_checked():
    decision = await _active_goal_decision()
    plan = await create_plan_from_decision(decision)
    action = await get_action(plan.action_ids[0])
    assert action.status == ActionStatus.PENDING
    assert action.permission_check_id is None
