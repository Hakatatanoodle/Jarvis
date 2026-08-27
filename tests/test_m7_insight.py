"""Integration tests for insight/api.py — M7's evidence-first weekly
Insight Engine."""
import pytest

from contracts.enums import GoalStatus, GoalType, InsightMode
from goals.api import create_goal, set_status as set_goal_status
from insight.api import generate_insight, get_insight, list_insights, record_user_response
from memory.api import create_memory
from contracts.enums import MemoryType
from mission.api import set_mission

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_quiet_week_is_honest_not_fabricated():
    await set_mission(title="Grow", statement="Statement.")
    record = await generate_insight(InsightMode.REVIEW)
    assert record.wins == []
    assert "No completed goals or actions" in record.problems[0]
    assert "quiet" in record.narrative.lower()


async def test_completed_goal_shows_up_as_a_win_with_evidence():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.TASK, title="Write M7 tests")
    await set_goal_status(g.id, GoalStatus.ACTIVE, reason="starting")
    await set_goal_status(g.id, GoalStatus.COMPLETED, reason="done")

    record = await generate_insight(InsightMode.REVIEW)
    assert any("Write M7 tests" in w for w in record.wins)
    assert any(g.id in c.supporting_ids for c in record.evidence)


async def test_every_claim_has_supporting_evidence():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.TASK, title="A task")
    await set_goal_status(g.id, GoalStatus.ACTIVE, reason="starting")
    await create_memory(type=MemoryType.FACT, title="T", value="V", source_ids=["s"])

    record = await generate_insight(InsightMode.REVIEW)
    for claim in record.evidence:
        assert len(claim.supporting_ids) > 0


async def test_reflection_and_review_share_evidence_different_tone():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.TASK, title="Shared evidence task")
    await set_goal_status(g.id, GoalStatus.ACTIVE, reason="starting")
    await set_goal_status(g.id, GoalStatus.COMPLETED, reason="done")

    reflection = await generate_insight(InsightMode.REFLECTION)
    review = await generate_insight(InsightMode.REVIEW)
    assert reflection.wins == review.wins  # same evidence pipeline (decision #3)
    assert reflection.narrative != review.narrative  # different tone/mode


async def test_scope_is_always_weekly():
    await set_mission(title="Grow", statement="Statement.")
    record = await generate_insight(InsightMode.REVIEW)
    assert record.scope.value == "weekly"


async def test_insight_is_persisted_and_listable():
    await set_mission(title="Grow", statement="Statement.")
    record = await generate_insight(InsightMode.REVIEW)

    fetched = await get_insight(record.id)
    assert fetched.id == record.id

    reviews_only = await list_insights(mode=InsightMode.REVIEW)
    assert len(reviews_only) == 1


async def test_user_response_is_the_only_mutable_field():
    await set_mission(title="Grow", statement="Statement.")
    record = await generate_insight(InsightMode.REVIEW)
    updated = await record_user_response(record.id, "Agreed, adjusting next week.")
    assert updated.user_response == "Agreed, adjusting next week."
    assert updated.narrative == record.narrative  # everything else untouched
