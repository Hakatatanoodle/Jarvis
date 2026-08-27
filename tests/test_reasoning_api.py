"""Integration tests for reasoning/api.py — M3's Reasoning Engine.
Verifies the pipeline actually runs end-to-end against real Goals/Memory,
and that the "simplest correct" choices (deterministic reasoning, no
caching, flat budget) behave sensibly, not that they're sophisticated."""
import pytest
from unittest.mock import AsyncMock, patch

from contracts.enums import GoalStatus, GoalType, MemoryType, Priority
from goals.api import create_goal, set_status
from memory.api import create_memory
from mission.api import set_mission
from reasoning.api import (
    NoActiveMissionError,
    UnknownIntentError,
    _asserts_unsupported_relationship,
    _asserts_wrong_mission,
    _asserts_wrong_status,
    gather_grounded_evidence,
    get_decision,
    list_decisions,
    reason,
)

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_reason_requires_active_mission():
    with pytest.raises(NoActiveMissionError):
        await reason(intent="Planning", objective="What should I work on?")


async def test_reason_rejects_unknown_intent():
    await set_mission(title="Grow", statement="Statement.")
    with pytest.raises(UnknownIntentError):
        await reason(intent="Programming", objective="anything")


async def test_planning_with_no_goals_is_honest_about_it():
    await set_mission(title="Grow", statement="Statement.")
    d = await reason(intent="Planning", objective="What should I work on today?")
    assert "No active or draft goals" in d.summary
    assert d.requires_plan is False
    assert d.goal_ids == []


async def test_planning_surfaces_the_highest_relevance_active_goal():
    await set_mission(title="Grow", statement="Statement.")
    low = await create_goal(type=GoalType.TASK, title="Low priority task", priority=Priority.LOW)
    high = await create_goal(type=GoalType.LIFE_GOAL, title="Financial independence", priority=Priority.CRITICAL)
    await set_status(low.id, GoalStatus.ACTIVE, reason="starting")
    await set_status(high.id, GoalStatus.ACTIVE, reason="starting")

    d = await reason(intent="Planning", objective="What should I work on today?")
    assert d.selected_option == "Financial independence"
    assert d.requires_plan is True
    assert high.id in d.goal_ids


async def test_decision_is_persisted_and_retrievable():
    await set_mission(title="Grow", statement="Statement.")
    d = await reason(intent="Planning", objective="What should I work on today?")

    fetched = await get_decision(d.id)
    assert fetched is not None
    assert fetched.id == d.id
    assert fetched.summary == d.summary


async def test_list_decisions_filters_by_intent():
    await set_mission(title="Grow", statement="Statement.")
    await reason(intent="Planning", objective="Plan objective")
    await reason(intent="Reflection", objective="Reflect objective")

    planning_only = await list_decisions(intent="Planning")
    assert len(planning_only) == 1
    assert planning_only[0].objective == "Plan objective"

    all_decisions = await list_decisions()
    assert len(all_decisions) == 2


async def test_reflection_summarizes_completed_and_active_goals():
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.PROJECT, title="Build Jarvis")
    await set_status(g.id, GoalStatus.ACTIVE, reason="starting")
    await set_status(g.id, GoalStatus.COMPLETED, reason="done")

    d = await reason(intent="Reflection", objective="How did this go?")
    assert "1 goal(s) completed" in d.summary
    assert d.requires_plan is False


async def test_decision_confidence_and_evidence_are_populated():
    # DE-08: every recommendation must be explainable — confidence isn't
    # a magic number and context_item_ids trace back to real evidence.
    await set_mission(title="Grow", statement="Statement.")
    g = await create_goal(type=GoalType.LIFE_GOAL, title="Financial independence")
    await set_status(g.id, GoalStatus.ACTIVE, reason="starting")

    d = await reason(intent="Planning", objective="What should I work on today?")
    assert 0.0 <= d.confidence <= 1.0
    assert len(d.context_item_ids) > 0
    assert len(d.reasoning) > 0


# --- BUG-M6-01: unsupported relationship inference --------------------
# Two independent goals (no parent_goal_id, no dependencies) must never
# be described as related, connected, or dependent on each other — not
# even by the LLM enhancement step. Evidence-first (PROJECT.md).

def _fake_goal_context_item(goal, reasoning: str):
    from contracts.context_item import ContextItem
    from contracts.enums import ContextSourceType

    return ContextItem(
        source_type=ContextSourceType.GOAL,
        source_id=goal.id,
        payload=goal.model_dump(mode="json"),
        relevance_score=0.5,
        confidence=1.0,
        freshness=1.0,
        reasoning=reasoning,
    )


def test_guard_rejects_relational_language_between_unrelated_goals():
    goal_a = type("G", (), {"id": "a", "model_dump": lambda self, mode: {
        "id": "a", "title": "Build Jarvis V1", "status": "Draft",
        "parent_goal_id": None, "dependencies": [],
    }})()
    goal_b = type("G", (), {"id": "b", "model_dump": lambda self, mode: {
        "id": "b", "title": "Financial Independence", "status": "Draft",
        "parent_goal_id": None, "dependencies": [],
    }})()
    items = [
        _fake_goal_context_item(goal_a, "Project 'Build Jarvis V1' is Draft."),
        _fake_goal_context_item(goal_b, "LifeGoal 'Financial Independence' is Draft."),
    ]
    hallucinated = (
        "Since Project 'Build Jarvis V1' is related to Financial Independence, "
        "work on both today."
    )
    assert _asserts_unsupported_relationship(hallucinated, items) is True


def test_guard_allows_relational_language_for_a_real_dependency():
    goal_a = type("G", (), {"id": "a", "model_dump": lambda self, mode: {
        "id": "a", "title": "Build Jarvis V1", "status": "Draft",
        "parent_goal_id": None, "dependencies": ["b"],
    }})()
    goal_b = type("G", (), {"id": "b", "model_dump": lambda self, mode: {
        "id": "b", "title": "Financial Independence", "status": "Draft",
        "parent_goal_id": None, "dependencies": [],
    }})()
    items = [
        _fake_goal_context_item(goal_a, "Project 'Build Jarvis V1' is Draft."),
        _fake_goal_context_item(goal_b, "LifeGoal 'Financial Independence' is Draft."),
    ]
    text = "'Build Jarvis V1' depends on Financial Independence, so start there."
    assert _asserts_unsupported_relationship(text, items) is False


def test_guard_ignores_text_with_no_relational_phrase():
    goal_a = type("G", (), {"id": "a", "model_dump": lambda self, mode: {
        "id": "a", "title": "Build Jarvis V1", "status": "Draft",
        "parent_goal_id": None, "dependencies": [],
    }})()
    items = [_fake_goal_context_item(goal_a, "Project 'Build Jarvis V1' is Draft.")]
    assert _asserts_unsupported_relationship("Focus on Build Jarvis V1 today.", items) is False


async def test_reason_falls_back_to_template_when_llm_invents_a_relationship():
    # Reproduces BUG-M6-01 end-to-end: two independent Draft goals, LLM
    # mocked to return exactly the hallucinated text from the bug report.
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build Jarvis V1")
    await create_goal(type=GoalType.LIFE_GOAL, title="Financial Independence")

    hallucinated = {
        "summary": "Since Project 'Build Jarvis V1' is related to Financial Independence, "
                    "work on both today.",
        "reasoning": "Build Jarvis V1 is connected to Financial Independence.",
    }
    with patch("reasoning.api.complete_json", new=AsyncMock(return_value=hallucinated)):
        d = await reason(intent="Planning", objective="What should I work on today?")

    assert "related" not in d.summary.lower()
    assert "connected" not in d.reasoning.lower()


# --- Mission/Goal conflation guard (found in dogfooding, 2026-08-10) --

def _fake_goal_item(title, status="Active"):
    from contracts.context_item import ContextItem
    from contracts.enums import ContextSourceType

    return ContextItem(
        source_type=ContextSourceType.GOAL, source_id="g1",
        payload={"title": title, "status": status},
        relevance_score=0.5, confidence=1.0, freshness=1.0, reasoning="",
    )


def test_guard_rejects_goal_relabeled_as_mission():
    mission = type("M", (), {"title": "I wanna be Ironman"})()
    items = [_fake_goal_item("Build jarvis v1")]
    bad = "Your active mission is to Build jarvis v1."  # the actual reported bug
    assert _asserts_wrong_mission(bad, mission, items) is True


def test_guard_allows_correct_mission_and_goal_mentioned_together():
    mission = type("M", (), {"title": "I wanna be Ironman"})()
    items = [_fake_goal_item("Build jarvis v1")]
    good = "Focus on 'Build jarvis v1' — it supports your mission 'I wanna be Ironman'."
    assert _asserts_wrong_mission(good, mission, items) is False


def test_guard_ignores_generic_mission_mention_with_no_conflation():
    mission = type("M", (), {"title": "I wanna be Ironman"})()
    items = [_fake_goal_item("Build jarvis v1")]
    assert _asserts_wrong_mission("Your mission is going well!", mission, items) is False


async def test_reason_falls_back_to_template_when_llm_conflates_goal_with_mission():
    # Real bug, real query shape: user asks about "missions," Mission
    # title was never in the LLM's evidence before this fix, so it
    # relabeled the top Goal as "the mission" instead — confidently wrong.
    await set_mission(title="I wanna be Ironman", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis v1")

    conflated = {
        "summary": "Your active mission is to Build jarvis v1.",
        "reasoning": "The goal Build jarvis v1 is currently Active.",
    }
    with patch("reasoning.api.complete_json", new=AsyncMock(return_value=conflated)):
        d = await reason(intent="Planning", objective="what missions do i have?")

    # The template is allowed to mention the goal — that's normal. What
    # must never survive is the conflated claim itself.
    assert d.summary != "Your active mission is to Build jarvis v1."
    assert "mission" not in d.summary.lower()


# --- Status-conflation guard (found in dogfooding, 2026-08-14) --------
# Draft and Active are distinct, mutually exclusive GoalStatus values
# (contracts/enums.py) — Draft never implies Active. The LLM enhancement
# once called two Draft goals "active" and justified it by misreading
# its own evidence back at the user.

def test_guard_rejects_goal_labeled_active_when_actually_draft():
    items = [_fake_goal_item("Build jarvis", status="Draft")]
    bad = "You have two active goals: Build jarvis and complete an ironman race."
    assert _asserts_wrong_status(bad, items) is True


def test_guard_allows_correct_active_status():
    items = [_fake_goal_item("Build jarvis", status="Active")]
    good = "You have one active goal: Build jarvis."
    assert _asserts_wrong_status(good, items) is False


def test_guard_ignores_text_with_no_active_claim():
    items = [_fake_goal_item("Build jarvis", status="Draft")]
    assert _asserts_wrong_status("Build jarvis is still in Draft.", items) is False


async def test_reason_falls_back_to_template_when_llm_calls_draft_goal_active():
    # Real bug, real query shape: "do i have any active goals?" against
    # two Draft goals — the LLM answered "yes, two active goals" and
    # self-contradicted its own evidence to justify it.
    await set_mission(title="Grow", statement="Statement.")
    await create_goal(type=GoalType.PROJECT, title="Build jarvis")
    await create_goal(type=GoalType.LIFE_GOAL, title="complete an ironman race")

    wrong_status = {
        "summary": "You have two active goals: Build jarvis and complete an ironman race.",
        "reasoning": "Both goals are listed with status 'Draft', indicating they are active.",
    }
    with patch("reasoning.api.complete_json", new=AsyncMock(return_value=wrong_status)):
        d = await reason(intent="Planning", objective="do i have any active goals?")

    assert "active goals" not in d.summary.lower()


# --- 2026-08-23c: whole-document co-occurrence false positive, found
# via live dogfooding of the grounded-conversation redesign — three
# consecutive real, accurate replies rejected 100% of the time. Root
# cause: both guards above originally checked "does the trigger phrase
# appear ANYWHERE + does a second goal title appear ANYWHERE", not
# whether they're actually predicated of each other. With exactly one
# Active goal and any Draft ones, virtually any accurate multi-goal
# answer satisfied the old check by coincidence. Fixed to require
# same-sentence co-occurrence — these tests prove the false positive is
# gone AND the original bug (same sentence) is still caught.

def test_status_guard_allows_accurate_multi_goal_mention_in_separate_sentences():
    items = [_fake_goal_item("Build Jarvis", status="Active"), _fake_goal_item("Run an ironman run", status="Draft")]
    accurate = (
        "Build Jarvis is your active project, so let's focus there today. "
        "Your other goal, Run an ironman run, is still in Draft for now."
    )
    assert _asserts_wrong_status(accurate, items) is False


def test_status_guard_still_rejects_same_sentence_violation_with_other_goals_present():
    # Confirms the sentence-level rewrite didn't lose the original bug's
    # detection just because a third, unrelated goal is also in scope.
    items = [
        _fake_goal_item("Build jarvis", status="Draft"),
        _fake_goal_item("complete an ironman race", status="Draft"),
    ]
    bad = "You have two active goals: Build jarvis and complete an ironman race."
    assert _asserts_wrong_status(bad, items) is True


def test_relationship_guard_allows_two_goals_in_separate_unrelated_sentences():
    goal_a = type("G", (), {"id": "a", "model_dump": lambda self, mode: {
        "id": "a", "title": "Build Jarvis V1", "status": "Active",
        "parent_goal_id": None, "dependencies": [],
    }})()
    goal_b = type("G", (), {"id": "b", "model_dump": lambda self, mode: {
        "id": "b", "title": "Financial Independence", "status": "Draft",
        "parent_goal_id": None, "dependencies": [],
    }})()
    items = [
        _fake_goal_context_item(goal_a, "Project 'Build Jarvis V1' is Active."),
        _fake_goal_context_item(goal_b, "LifeGoal 'Financial Independence' is Draft."),
    ]
    accurate = (
        "Build Jarvis V1 is your active project, and it affects how much free time "
        "you'll have. Financial Independence is still a Draft goal for later."
    )
    assert _asserts_unsupported_relationship(accurate, items) is False


# --- V1-M3: Memory as evidence (acceptance criterion J) ---
# reasoning/policies.py already pulled Active memories into Planning/
# Reflection candidates before V1-M3 existed — this proves that wiring
# actually produces evidence a Decision cites, since nothing here
# previously exercised it end-to-end.

async def test_reflection_surfaces_active_memory_as_evidence():
    await set_mission(title="Grow", statement="Statement.")
    await create_memory(
        type=MemoryType.PREFERENCE,
        title="user's music preference",
        value="rock music while coding",
        source_ids=["src-1"],
    )

    d = await reason(intent="Reflection", objective="How's it going?")

    assert "1 relevant memories on record" in d.summary
    assert len(d.context_item_ids) == 1


async def test_reflection_with_no_memories_says_so_honestly():
    await set_mission(title="Grow", statement="Statement.")

    d = await reason(intent="Reflection", objective="How's it going?")

    assert "0 relevant memories on record" in d.summary
    assert len(d.context_item_ids) == 0


async def test_reflection_counts_multiple_active_memories_not_forgotten_ones():
    await set_mission(title="Grow", statement="Statement.")
    await create_memory(
        type=MemoryType.FACT, title="user's name", value="Yochan", source_ids=["src-1"]
    )
    kept = await create_memory(
        type=MemoryType.PREFERENCE, title="editor", value="VS Code", source_ids=["src-2"]
    )
    forgotten = await create_memory(
        type=MemoryType.PREFERENCE, title="old preference", value="Vim", source_ids=["src-3"]
    )
    from memory.api import forget_memory
    await forget_memory(forgotten.id, reason="superseded")

    d = await reason(intent="Reflection", objective="How's it going?")

    assert "2 relevant memories on record" in d.summary
    assert len(d.context_item_ids) == 2


# --- regression: 2026-08-27 dogfooding, round 4 -------------------------
# Live session: mission was superseded via M2's natural-language mission-
# change flow ("add a mission titled Test mission" -> confirmed -> v2),
# and every subsequent "list my goals" / reasoning-routed question
# genuinely, faithfully reported zero goals — not a hallucination, the
# evidence handed to the model really was empty. Root cause:
# gather_grounded_evidence() called reflection_policy(mission.id) — the
# CURRENT VERSION ROW's own id — instead of mission.identity_id, the
# stable identity goals.api.create_goal actually stores on every Goal.
# For a mission's first-ever version these happen to be equal
# (mission.api.set_mission sets identity_id = id on creation), which is
# exactly why this survived the entire pre-existing test suite and the
# first three rounds of dogfooding: nothing had superseded a mission
# yet. reason() below already used mission.identity_id correctly
# (RETRIEVAL_POLICIES[intent](mission.identity_id)) — gather_grounded_
# evidence just didn't match it. This bug predates V1-M2 and isn't
# specific to it; M2's natural-language mission-change confirmation
# flow is simply what exercised mission supersession, in practice, for
# the first time.
#
# Also found in the same audit, NOT fixed here — flagged instead of
# guessed at: insight/api.py's _gather_evidence has the identical
# `g.mission_id != mission.id` comparison pattern (its "misaligned
# goals" / RV-08 check). Unlike this one, that's not a simple typo fix:
# the claim text says it's meant to detect goals created under a STALE
# mission VERSION, but Goal only ever stores the stable identity_id,
# never a version snapshot — so swapping in identity_id there would
# just silently disable the check (always empty) rather than correctly
# implement it (which would need a new field on Goal recording the
# mission version active at creation time, a real schema decision, not
# a bug fix).

async def test_gather_grounded_evidence_finds_goals_after_mission_is_superseded():
    await set_mission(title="Grow", statement="Original.")
    goal = await create_goal(type=GoalType.PROJECT, title="Build Jarvis")
    await set_status(goal.id, GoalStatus.ACTIVE, reason="setup")

    # Supersede the mission — exactly what M2's mission-change
    # confirmation flow does once approved (mission.api.set_mission
    # with an existing mission already active).
    mission_v2 = await set_mission(title="New Mission", statement="Different statement.")
    assert mission_v2.id != mission_v2.identity_id  # confirms the divergence this bug depended on

    context_items, evidence = await gather_grounded_evidence(mission_v2)

    assert len(context_items) >= 1
    assert "Build Jarvis" in evidence
    assert "Active" in evidence


async def test_gather_grounded_evidence_finds_goals_created_both_before_and_after_supersession():
    await set_mission(title="Grow", statement="Original.")
    before = await create_goal(type=GoalType.PROJECT, title="Created before the change")

    mission_v2 = await set_mission(title="New Mission", statement="Different statement.")
    after = await create_goal(type=GoalType.TASK, title="Created after the change")

    context_items, evidence = await gather_grounded_evidence(mission_v2)

    assert "Created before the change" in evidence
    assert "Created after the change" in evidence
