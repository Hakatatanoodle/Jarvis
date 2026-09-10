"""Integration tests for M5: capability registry, primitives, and real
Action execution end-to-end.

Calendar tests mock infra.mcp_client.call_calendar_tool (patched at
capabilities.primitives.calendar_ops's import site) rather than hitting
a real calendar-mcp server/Google account — this suite has no OAuth
credentials and shouldn't require one to run. infra/mcp_client.py's own
parsing/error-classification logic is unit-tested separately in
tests/test_mcp_client.py against fake tool-result objects, so the two
together cover "the executor calls the right tool with the right args
and handles the response" (here) and "the client parses a raw MCP
result correctly" (there) without needing a live server for either.
"""
import pytest
from unittest.mock import AsyncMock, patch

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
    assert ids == {"fs.read", "fs.write", "calendar.read_events", "calendar.create_event", "goals.advance"}


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


async def test_fs_read_write_end_to_end(tmp_path):
    import capabilities.primitives.fs_ops as fs_ops
    fs_ops.configure_for_test([tmp_path], [])
    target = str(tmp_path / "note.txt")

    action = await _make_plan_action("fs.write", ActionType.WRITE, {"path": target, "content": "hello"})
    cap, _ = get_capability("fs.write")
    pcr = await check_permission(action.id, cap)
    assert pcr.permission_status == PermissionStatus.CONFIRMATION_REQUIRED  # force_confirmation, unconditional
    await confirm(pcr.id)
    result = await run_action(action.id)
    assert result.success is True

    read_action = await _make_plan_action("fs.read", ActionType.READ, {"path": target})
    cap, _ = get_capability("fs.read")
    read_pcr = await check_permission(read_action.id, cap)
    assert read_pcr.permission_status == PermissionStatus.GRANTED  # reads never confirm
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
    # Mocked at the calendar_ops import site, not infra.mcp_client itself
    # — patching the name the executor actually calls, same discipline
    # as every other mock.patch target in this codebase.
    fake_result = {"id": "google-event-abc123", "summary": "Standup"}
    with patch("capabilities.primitives.calendar_ops.call_calendar_tool", new=AsyncMock(return_value=fake_result)) as mock_call:
        result = await run_action(action.id)
    assert result.success is True
    assert result.output["event_id"] == "google-event-abc123"
    mock_call.assert_awaited_once_with(
        "create-event",
        {"summary": "Standup", "start": "2026-08-10T09:00:00+00:00", "end": "2026-08-10T09:15:00+00:00", "calendarId": "primary"},
    )

    action_after = await get_action(action.id)
    assert action_after.status == ActionStatus.COMPLETED


async def test_calendar_create_event_daily_recurrence_sends_rrule():
    # First real (non-test-double) user of the generic extra_parameters
    # mechanism — proves calendar_ops.py's RRULE mapping end-to-end.
    action = await _make_plan_action(
        "calendar.create_event", ActionType.WRITE,
        {"title": "Standup", "start": "2026-08-10T09:00:00+00:00", "end": "2026-08-10T09:15:00+00:00",
         "recurrence": "daily"},
    )
    cap, _ = get_capability("calendar.create_event")
    pcr = await check_permission(action.id, cap)
    await confirm(pcr.id)

    fake_result = {"id": "google-event-daily"}
    with patch("capabilities.primitives.calendar_ops.call_calendar_tool", new=AsyncMock(return_value=fake_result)) as mock_call:
        result = await run_action(action.id)
    assert result.success is True
    mock_call.assert_awaited_once_with(
        "create-event",
        {"summary": "Standup", "start": "2026-08-10T09:00:00+00:00", "end": "2026-08-10T09:15:00+00:00",
         "calendarId": "primary", "recurrence": ["RRULE:FREQ=DAILY"]},
    )


async def test_calendar_create_event_recurrence_none_omits_rrule():
    # "none" behaves exactly like omitting recurrence entirely — same
    # args dict the pre-existing test_calendar_create_event_requires_
    # confirmation_then_executes above already pins.
    action = await _make_plan_action(
        "calendar.create_event", ActionType.WRITE,
        {"title": "Standup", "start": "2026-08-10T09:00:00+00:00", "end": "2026-08-10T09:15:00+00:00",
         "recurrence": "none"},
    )
    cap, _ = get_capability("calendar.create_event")
    pcr = await check_permission(action.id, cap)
    await confirm(pcr.id)

    fake_result = {"id": "google-event-oneoff"}
    with patch("capabilities.primitives.calendar_ops.call_calendar_tool", new=AsyncMock(return_value=fake_result)) as mock_call:
        await run_action(action.id)
    mock_call.assert_awaited_once_with(
        "create-event",
        {"summary": "Standup", "start": "2026-08-10T09:00:00+00:00", "end": "2026-08-10T09:15:00+00:00",
         "calendarId": "primary"},
    )


async def test_calendar_read_events_low_risk_granted_immediately():
    action = await _make_plan_action(
        "calendar.read_events", ActionType.READ,
        {"start": "2026-08-10T00:00:00+00:00", "end": "2026-08-17T00:00:00+00:00"},
    )
    cap, _ = get_capability("calendar.read_events")
    pcr = await check_permission(action.id, cap)
    assert pcr.permission_status == PermissionStatus.GRANTED

    fake_result = {"events": [{"id": "evt-1", "summary": "Standup"}]}
    with patch("capabilities.primitives.calendar_ops.call_calendar_tool", new=AsyncMock(return_value=fake_result)):
        result = await run_action(action.id)
    assert result.success is True
    assert result.output["events"] == [{"id": "evt-1", "summary": "Standup"}]


async def test_calendar_create_event_surfaces_auth_error_as_failure_not_crash():
    """The whole point of CalendarAuthError (infra/mcp_client.py) is
    that it's a normal Exception run_action already knows how to catch
    and turn into a failed CapabilityResult with a readable .error —
    not a special case dispatch.py or action_engine.api needs new
    handling for."""
    from infra.mcp_client import CalendarAuthError

    action = await _make_plan_action(
        "calendar.create_event", ActionType.WRITE,
        {"title": "Standup", "start": "2026-08-10T09:00:00+00:00", "end": "2026-08-10T09:15:00+00:00"},
    )
    cap, _ = get_capability("calendar.create_event")
    pcr = await check_permission(action.id, cap)
    await confirm(pcr.id)

    with patch(
        "capabilities.primitives.calendar_ops.call_calendar_tool",
        new=AsyncMock(side_effect=CalendarAuthError("token expired, re-auth at http://127.0.0.1:3000/accounts")),
    ):
        result = await run_action(action.id)
    assert result.success is False
    assert "re-auth" in result.error

    action_after = await get_action(action.id)
    assert action_after.status == ActionStatus.FAILED


async def test_goals_advance_touches_the_real_goal():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.TASK, title="Write M5 tests")

    action = await _make_plan_action("goals.advance", ActionType.COMPUTE, {"goal_id": g.id, "note": "checked in"})
    cap, _ = get_capability("goals.advance")
    await check_permission(action.id, cap)
    result = await run_action(action.id)

    assert result.success is True
    assert result.output["version"] == 2  # bumped from 1


async def test_run_action_fails_gracefully_on_bad_input(tmp_path):
    import capabilities.primitives.fs_ops as fs_ops
    fs_ops.configure_for_test([tmp_path], [])
    action = await _make_plan_action("fs.read", ActionType.READ, {"path": str(tmp_path / "does_not_exist.txt")})
    cap, _ = get_capability("fs.read")
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
