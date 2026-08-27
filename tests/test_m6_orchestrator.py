"""Integration tests for orchestrator/api.py — M6's first real end-to-end
request loop. Three distinct request types per §11's Definition of Done:
auto-granted Planning, informational Reflection, and a confirmation-
required action.

Note on the confirmation-path test: Planner (M4) only ever compiles
goals.advance actions (Low/Medium risk, always auto-granted today) — see
ARCHITECTURE_ISSUES.md's M6 entry. To prove Orchestrator's own gating
logic (not Planner's, which isn't scoped to pick capabilities
intelligently in V0), that test injects a higher-risk action directly
onto an Orchestrator-created Plan rather than waiting for Planner to
naturally produce one."""
import pytest

import capabilities.bootstrap  # noqa: F401
from action_engine.api import create_action, get_action
from contracts.enums import ActionStatus, ActionType, GoalStatus, GoalType
from goals.api import create_goal, set_status as set_goal_status
from mission.api import set_mission
from orchestrator.api import resume_action, run_request

pytestmark = pytest.mark.usefixtures("clean_db")


async def _seed_active_life_goal(title="Financial independence"):
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.LIFE_GOAL, title=title)
    await set_goal_status(g.id, GoalStatus.ACTIVE, reason="starting")
    return g


async def test_planning_request_runs_end_to_end_and_auto_completes():
    await _seed_active_life_goal()
    result = await run_request("What should I work on today?")

    assert result.intent == "Planning"
    assert result.plan is not None
    assert len(result.completed) == len(result.plan.action_ids)
    assert result.awaiting_confirmation == []
    assert result.denied == []


async def test_reflection_request_is_informational_no_plan():
    await _seed_active_life_goal()
    result = await run_request("How did this week go?")

    assert result.intent == "Reflection"
    assert result.plan is None
    assert result.completed == []


async def test_confirmation_required_action_pauses_and_resumes():
    await _seed_active_life_goal()
    result = await run_request("What should I work on today?")
    plan = result.plan

    # Inject a higher-risk action directly (see module docstring for why).
    risky_action = await create_action(
        plan_id=plan.id, capability_id="calendar.create_event", type=ActionType.WRITE,
        title="Schedule a review", parameters={
            "title": "Review", "start": "2026-08-10T09:00:00+00:00", "end": "2026-08-10T09:30:00+00:00",
        },
    )
    from orchestrator.api import _handle_action
    bucket, pcr = await _handle_action(risky_action.id)

    assert bucket == "awaiting_confirmation"
    action = await get_action(risky_action.id)
    assert action.status == ActionStatus.READY  # reached Ready, blocked before Running

    capability_result = await resume_action(pcr.id, approved=True)
    assert capability_result.success is True
    action_after = await get_action(risky_action.id)
    assert action_after.status == ActionStatus.COMPLETED


async def test_unregistered_capability_fails_gracefully_not_crash():
    await _seed_active_life_goal()
    result = await run_request("What should I work on today?")
    plan = result.plan

    from orchestrator.api import _handle_action
    stray_action = await create_action(
        plan_id=plan.id, capability_id="nonexistent.thing", type=ActionType.COMPUTE, title="t", parameters={},
    )
    bucket, payload = await _handle_action(stray_action.id)
    assert bucket == "failed"
    assert "not registered" in payload["reason"]


async def test_reject_leaves_action_cancelled():
    await _seed_active_life_goal()
    result = await run_request("What should I work on today?")
    plan = result.plan

    risky_action = await create_action(
        plan_id=plan.id, capability_id="calendar.create_event", type=ActionType.WRITE,
        title="Schedule a review", parameters={
            "title": "Review", "start": "2026-08-10T09:00:00+00:00", "end": "2026-08-10T09:30:00+00:00",
        },
    )
    from orchestrator.api import _handle_action
    _, pcr = await _handle_action(risky_action.id)

    await resume_action(pcr.id, approved=False)
    action_after = await get_action(risky_action.id)
    assert action_after.status == ActionStatus.CANCELLED
