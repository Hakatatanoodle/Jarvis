"""Unit tests for state_change/extraction.py. complete_json() is mocked
— this tests the deterministic re-validation + entity-resolution layer,
not any real model, matching how tests/test_memory_extraction.py mocks
the same LLM boundary."""
from unittest.mock import AsyncMock, patch

from contracts.enums import GoalStatus, GoalType
from contracts.state_change_candidate import StateChangeOperation
from state_change.extraction import extract_candidate

_KNOWN_GOALS = [
    ("Build Jarvis", "goal-1", "Active"),
    ("Run a marathon", "goal-2", "Draft"),
]


async def test_create_goal_extracted():
    raw = {"operation": "create_goal", "title": "Learn Spanish", "type": "Habit", "confidence": 0.9}
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("I want to create a goal to learn Spanish")
    assert c.operation is StateChangeOperation.CREATE_GOAL
    assert c.title == "Learn Spanish"
    assert c.type is GoalType.HABIT


async def test_create_goal_with_null_type_kept_not_dropped():
    # extraction is told not to force a guess — null type is a valid,
    # meaningful output the policy layer handles (ASK_CLARIFY), not a
    # validation failure.
    raw = {"operation": "create_goal", "title": "Something vague", "type": None, "confidence": 0.6}
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("create a goal for something vague")
    assert c is not None
    assert c.type is None


async def test_create_goal_with_empty_string_type_normalized_to_none():
    # Dogfooding, 2026-08-26: small/cheap models often emit "" rather
    # than a literal JSON null for an omitted optional field. Before the
    # fix this dropped the WHOLE candidate (type "" not in _VALID_TYPES),
    # silently discarding an explicit "add a goal named X" instruction.
    raw = {"operation": "create_goal", "title": "exam overloaded", "type": "", "confidence": 0.8}
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("could you add a new goal named exam overloaded")
    assert c is not None
    assert c.type is None
    assert c.title == "exam overloaded"


async def test_set_goal_status_resolves_exact_verbatim_title():
    raw = {"operation": "set_goal_status", "goal_ref": "Build Jarvis", "new_status": "Paused", "confidence": 0.85}
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("pause my build jarvis goal", known_goals=_KNOWN_GOALS)
    assert c.operation is StateChangeOperation.SET_GOAL_STATUS
    assert c.match_count == 1
    assert c.resolved_goal_id == "goal-1"
    assert c.resolved_goal_status is GoalStatus.ACTIVE
    assert c.new_status is GoalStatus.PAUSED


async def test_set_goal_status_case_insensitive_match():
    raw = {"operation": "set_goal_status", "goal_ref": "build jarvis", "new_status": "Paused", "confidence": 0.8}
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("pause build jarvis", known_goals=_KNOWN_GOALS)
    assert c.match_count == 1
    assert c.resolved_goal_id == "goal-1"


async def test_set_goal_status_no_match_yields_zero_match_count():
    raw = {"operation": "set_goal_status", "goal_ref": "Nonexistent Goal", "new_status": "Paused", "confidence": 0.5}
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("pause my nonexistent goal", known_goals=_KNOWN_GOALS)
    assert c.match_count == 0
    assert c.resolved_goal_id is None


async def test_set_goal_status_duplicate_titles_yields_ambiguous_match_count():
    dup_goals = _KNOWN_GOALS + [("Build Jarvis", "goal-3", "Draft")]
    raw = {"operation": "set_goal_status", "goal_ref": "Build Jarvis", "new_status": "Paused", "confidence": 0.8}
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("pause build jarvis", known_goals=dup_goals)
    assert c.match_count == 2
    assert c.resolved_goal_id is None


async def test_set_mission_extracted():
    raw = {"operation": "set_mission", "mission_title": "Grow", "mission_statement": "Become better.", "confidence": 0.9}
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("change my mission to Grow: Become better.")
    assert c.operation is StateChangeOperation.SET_MISSION
    assert c.mission_title == "Grow"
    assert c.mission_statement == "Become better."


async def test_null_operation_is_no_candidate():
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value={"operation": None})):
        c = await extract_candidate("just chatting, not a command")
    assert c is None


async def test_invalid_status_value_fails_closed():
    raw = {"operation": "set_goal_status", "goal_ref": "Build Jarvis", "new_status": "Deleted", "confidence": 0.8}
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("delete build jarvis", known_goals=_KNOWN_GOALS)
    assert c is None


async def test_create_goal_missing_title_fails_closed():
    raw = {"operation": "create_goal", "title": "", "type": "Task", "confidence": 0.8}
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value=raw)):
        c = await extract_candidate("create a goal")
    assert c is None


async def test_llm_unavailable_fails_closed_to_none():
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value=None)):
        c = await extract_candidate("pause my goal", known_goals=_KNOWN_GOALS)
    assert c is None


async def test_venting_is_not_extracted_as_a_command():
    # Explicit-only rule, same as memory extraction: the prompt is told
    # never to infer a command from mood/venting. This test documents
    # the expectation at the validation boundary — a well-behaved model
    # returns operation=None for venting, which must yield no candidate.
    with patch("state_change.extraction.complete_json", new=AsyncMock(return_value={"operation": None})):
        c = await extract_candidate("I don't feel like building jarvis right now", known_goals=_KNOWN_GOALS)
    assert c is None
