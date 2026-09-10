"""Integration tests for goals/api.py against a real Postgres instance —
these are what §9 M1 means by 'tests against §6.2 invariants': no
dependency cycles, no circular parents, every Goal traces to exactly one
Mission, and it cannot exist without an active one."""
import pytest

from contracts.enums import GoalStatus, GoalType, Priority
from goals.api import (
    DependencyCycleError,
    GoalNotFoundError,
    InvalidGoalTransitionError,
    NoActiveMissionError,
    ParentCycleError,
    add_dependency,
    create_goal,
    get_goal_history,
    remove_dependency,
    set_parent,
    set_status,
    update_goal,
)
from mission.api import set_mission

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_create_goal_requires_active_mission():
    with pytest.raises(NoActiveMissionError):
        await create_goal(type=GoalType.PROJECT, title="Build Jarvis")


async def test_create_goal_uses_active_mission():
    mission = await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.PROJECT, title="Build Jarvis")
    assert g.mission_id == mission.id
    assert g.status == GoalStatus.DRAFT
    assert g.version == 1


async def test_child_goal_inherits_mission_from_parent():
    await set_mission(title="Grow", statement="Statement.")
    parent = await create_goal(type=GoalType.LIFE_GOAL, title="Financial independence")
    child = await create_goal(type=GoalType.PROJECT, title="Build Jarvis", parent_goal_id=parent.id)
    assert child.mission_id == parent.mission_id


async def test_dependency_cycle_rejected():
    await set_mission(title="Grow", statement="Statement.")
    a = await create_goal(type=GoalType.TASK, title="A")
    b = await create_goal(type=GoalType.TASK, title="B")

    a = await add_dependency(a.id, b.id, reason="A needs B done first")

    with pytest.raises(DependencyCycleError):
        await add_dependency(b.id, a.id, reason="trying to create a cycle")


async def test_self_dependency_rejected():
    await set_mission(title="Grow", statement="Statement.")
    a = await create_goal(type=GoalType.TASK, title="A")
    with pytest.raises(DependencyCycleError):
        await add_dependency(a.id, a.id, reason="self dependency")


async def test_transitive_dependency_cycle_rejected():
    await set_mission(title="Grow", statement="Statement.")
    a = await create_goal(type=GoalType.TASK, title="A")
    b = await create_goal(type=GoalType.TASK, title="B")
    c = await create_goal(type=GoalType.TASK, title="C")

    await add_dependency(a.id, b.id, reason="a needs b")
    await add_dependency(b.id, c.id, reason="b needs c")

    # c -> a would close the loop a -> b -> c -> a
    with pytest.raises(DependencyCycleError):
        await add_dependency(c.id, a.id, reason="trying to close the loop")


async def test_parent_cycle_rejected():
    await set_mission(title="Grow", statement="Statement.")
    a = await create_goal(type=GoalType.LIFE_GOAL, title="A")
    b = await create_goal(type=GoalType.PROJECT, title="B", parent_goal_id=a.id)
    c = await create_goal(type=GoalType.TASK, title="C", parent_goal_id=b.id)

    # A is an ancestor of C (A -> B -> C). Making C the parent of A would cycle.
    with pytest.raises(ParentCycleError):
        await set_parent(a.id, c.id, reason="trying to create a cycle")


async def test_self_parent_rejected():
    await set_mission(title="Grow", statement="Statement.")
    a = await create_goal(type=GoalType.TASK, title="A")
    with pytest.raises(ParentCycleError):
        await set_parent(a.id, a.id, reason="self parent")


async def test_reparent_survives_a_mission_edit():
    # Was test_reparent_across_missions_rejected. Pre-fix (2026-08-07),
    # this only "passed" because it was accidentally relying on the same
    # bug fixed in ARCHITECTURE_ISSUES.md that day: set_mission() calling
    # "Mission 2" right after "Mission 1" doesn't create an independent
    # second mission — under this architecture there's only ever ONE
    # active mission, and set_mission() always supersedes the current one
    # (same identity, new version). So this was never actually testing
    # "different missions" (V0 has no way to construct two independent
    # ones through the public API) — it was testing "goals created before
    # vs after a mission edit," and got a ValueError only because
    # Goal.mission_id stored the per-version id, not the stable identity.
    # Now that it stores the identity, reparenting across a mere edit
    # correctly succeeds — that's the fix, not a regression.
    await set_mission(title="Mission 1", statement="Statement 1.")
    old_top = await create_goal(type=GoalType.LIFE_GOAL, title="Old top-level goal")

    await set_mission(title="Mission 2", statement="Statement 2.")
    new_top = await create_goal(type=GoalType.LIFE_GOAL, title="New top-level goal")

    updated = await set_parent(old_top.id, new_top.id, reason="mission was only edited, not migrated")
    assert updated.parent_goal_id == new_top.id


async def test_valid_status_transition_chain():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.TASK, title="Task")

    g = await set_status(g.id, GoalStatus.ACTIVE, reason="starting work")
    assert g.status == GoalStatus.ACTIVE

    g = await set_status(g.id, GoalStatus.PAUSED, reason="taking a break")
    assert g.status == GoalStatus.PAUSED

    g = await set_status(g.id, GoalStatus.ACTIVE, reason="resuming")
    assert g.status == GoalStatus.ACTIVE

    g = await set_status(g.id, GoalStatus.COMPLETED, reason="done")
    assert g.status == GoalStatus.COMPLETED
    assert g.completed_at is not None


async def test_invalid_status_transition_rejected():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.TASK, title="Task")
    g = await set_status(g.id, GoalStatus.ACTIVE, reason="starting work")
    g = await set_status(g.id, GoalStatus.COMPLETED, reason="done")

    with pytest.raises(InvalidGoalTransitionError):
        await set_status(g.id, GoalStatus.ACTIVE, reason="should not be allowed")


async def test_update_bumps_version_and_records_history():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.TASK, title="Original title")
    assert g.version == 1

    g = await update_goal(g.id, reason="typo fix", title="Corrected title")
    assert g.version == 2
    assert g.title == "Corrected title"

    history = await get_goal_history(g.id)
    assert len(history) == 1
    assert history[0]["version"] == 1
    assert history[0]["snapshot"]["title"] == "Original title"
    assert history[0]["reason"] == "typo fix"


async def test_remove_dependency_is_idempotent_and_works():
    await set_mission(title="Grow", statement="Statement.")
    a = await create_goal(type=GoalType.TASK, title="A")
    b = await create_goal(type=GoalType.TASK, title="B")
    a = await add_dependency(a.id, b.id, reason="needs b")
    assert b.id in a.dependencies

    a = await remove_dependency(a.id, b.id, reason="no longer needed")
    assert b.id not in a.dependencies


async def test_operations_on_nonexistent_goal_raise():
    await set_mission(title="Grow", statement="Statement.")
    with pytest.raises(GoalNotFoundError):
        await set_status("00000000-0000-0000-0000-000000000000", GoalStatus.ACTIVE, reason="x")
