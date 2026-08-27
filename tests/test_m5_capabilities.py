"""Integration tests for M5: capability registry, primitives, and real
Action execution end-to-end."""
import pytest

import capabilities.bootstrap  # noqa: F401 — registers all primitives
from action_engine.api import (
    CapabilityNotFoundError, PermissionNotSatisfiedError, create_action, get_action, run_action,
)
from capabilities.registry import get as get_capability, list_capabilities
from contracts.enums import ActionStatus, ActionType, GoalStatus, GoalType, PermissionStatus
from goals.api import create_goal
from mission.api import set_mission
from permission.api import check_permission, confirm

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_five_primitives_registered():
    ids = {c.id for c in list_capabilities()}
    assert ids == {"file.read", "file.write", "calendar.read_events", "calendar.create_event", "goals.advance"}


async def _real_plan_id() -> str:
    # Reuse the real M1-M4 pipeline to get a valid plan_id (Plan has a
    # real FK on decision_id) instead of hand-rolling fragile SQL.
    from goals.api import set_status as set_goal_status
    from reasoning.api import reason
    from planner.api import create_plan_from_decision

    mission = await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.LIFE_GOAL, title="Seed goal for M5 tests")
    await set_goal_status(g.id, GoalStatus.ACTIVE, reason="starting")
    decision = await reason(intent="Planning", objective="seed")
    plan = await create_plan_from_decision(decision)
    return plan.id


async def _make_plan_action(capability_id, action_type, parameters, plan_id=None):
    if plan_id is None:
        plan_id = await _real_plan_id()
    return await create_action(plan_id=plan_id, capability_id=capability_id, type=action_type, title="t", parameters=parameters)


async def test_file_read_write_end_to_end():
    action = await _make_plan_action("file.write", ActionType.WRITE, {"filename": "note.txt", "content": "hello"})
    cap, _ = get_capability("file.write")
    await check_permission(action.id, cap)
    result = await run_action(action.id)
    assert result.success is True

    read_action = await _make_plan_action("file.read", ActionType.READ, {"filename": "note.txt"})
    cap, _ = get_capability("file.read")
    await check_permission(read_action.id, cap)
    read_result = await run_action(read_action.id)
    assert read_result.output["content"] == "hello"


async def test_calendar_create_event_requires_confirmation_then_executes():
    action = await _make_plan_action(
        "calendar.create_event", ActionType.WRITE,
        {"title": "Standup", "start": "2026-08-10T09:00:00+00:00", "end": "2026-08-10T09:15:00+00:00"},
    )
    cap, _ = get_capability("calendar.create_event")
    pcr = await check_permission(action.id, cap)
    assert pcr.permission_status == PermissionStatus.CONFIRMATION_REQUIRED

    with pytest.raises(PermissionNotSatisfiedError):
        await run_action(action.id)

    await confirm(pcr.id)
    result = await run_action(action.id)
    assert result.success is True
    assert "event_id" in result.output

    action_after = await get_action(action.id)
    assert action_after.status == ActionStatus.COMPLETED


async def test_goals_advance_touches_the_real_goal():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.TASK, title="Write M5 tests")

    action = await _make_plan_action("goals.advance", ActionType.COMPUTE, {"goal_id": g.id, "note": "checked in"})
    cap, _ = get_capability("goals.advance")
    await check_permission(action.id, cap)
    result = await run_action(action.id)

    assert result.success is True
    assert result.output["version"] == 2  # bumped from 1


async def test_run_action_fails_gracefully_on_bad_input():
    action = await _make_plan_action("file.read", ActionType.READ, {"filename": "does_not_exist.txt"})
    cap, _ = get_capability("file.read")
    await check_permission(action.id, cap)
    result = await run_action(action.id)

    assert result.success is False
    assert result.error is not None
    action_after = await get_action(action.id)
    assert action_after.status == ActionStatus.FAILED
    assert action_after.retry_count == 1  # 2 attempts, 0-indexed


async def test_run_action_rejects_unknown_capability():
    action = await _make_plan_action("nonexistent.capability", ActionType.COMPUTE, {})
    from contracts.capability import Capability
    from contracts.enums import CapabilityType, RiskLevel
    fake_cap = Capability(
        id="nonexistent.capability", name="x", description="x", category="x",
        capability_type=CapabilityType.PRIMITIVE, baseline_risk_level=RiskLevel.LOW,
    )
    await check_permission(action.id, fake_cap)
    with pytest.raises(CapabilityNotFoundError):
        await run_action(action.id)
