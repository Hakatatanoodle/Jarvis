"""Tests for the M2 deterministic trigger: LifeGoal/Project becoming
Active always gets remembered (constitution.md's rule-based always-store
list), wired via goals/api.py's set_status calling memory.api.remember_fact."""
import pytest

from contracts.enums import GoalStatus, GoalType, MemoryStatus, MemoryType
from goals.api import create_goal, set_status
from memory.api import list_memories
from mission.api import set_mission

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_activating_life_goal_creates_a_memory():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.LIFE_GOAL, title="Financial independence")
    await set_status(g.id, GoalStatus.ACTIVE, reason="starting now")

    memories = await list_memories(status=MemoryStatus.ACTIVE, type=MemoryType.FACT)
    assert len(memories) == 1
    assert "Financial independence" in memories[0].title
    assert g.id in memories[0].related_goal_ids


async def test_activating_project_creates_a_memory():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.PROJECT, title="Build Jarvis")
    await set_status(g.id, GoalStatus.ACTIVE, reason="starting now")

    memories = await list_memories(status=MemoryStatus.ACTIVE, type=MemoryType.FACT)
    assert len(memories) == 1
    assert memories[0].title == "Active project: Build Jarvis"


async def test_activating_task_does_not_trigger_a_memory():
    # Constitution's rule-based list is "long-term goals" and "active
    # projects" specifically — not every Goal type. A Task or Habit
    # becoming Active shouldn't create a Memory.
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.TASK, title="Write the goal contract")
    await set_status(g.id, GoalStatus.ACTIVE, reason="starting now")

    memories = await list_memories(status=MemoryStatus.ACTIVE, type=MemoryType.FACT)
    assert len(memories) == 0


async def test_pausing_and_reactivating_merges_not_duplicates():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.PROJECT, title="Build Jarvis")
    await set_status(g.id, GoalStatus.ACTIVE, reason="starting now")
    await set_status(g.id, GoalStatus.PAUSED, reason="taking a break")
    await set_status(g.id, GoalStatus.ACTIVE, reason="resuming")

    memories = await list_memories(status=MemoryStatus.ACTIVE, type=MemoryType.FACT)
    assert len(memories) == 1  # merged, not duplicated, across two activations
