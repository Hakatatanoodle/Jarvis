"""Pure unit tests — no DB, no LLM. state_change/policy.py is
deterministic Python by design (same shape as memory/candidate_policy.py),
so it should be fully testable without either."""
from contracts.enums import GoalStatus, GoalType
from contracts.state_change_candidate import StateChangeCandidate, StateChangeOperation
from state_change.policy import StateChangePolicyOutcome, decide


def _create(**overrides) -> StateChangeCandidate:
    defaults = dict(operation=StateChangeOperation.CREATE_GOAL, raw_user_text="create a goal to run a marathon",
                     title="Run a marathon", type=GoalType.LIFE_GOAL)
    defaults.update(overrides)
    return StateChangeCandidate(**defaults)


def _status_change(**overrides) -> StateChangeCandidate:
    defaults = dict(operation=StateChangeOperation.SET_GOAL_STATUS, raw_user_text="pause my build jarvis goal",
                     goal_ref="Build Jarvis", new_status=GoalStatus.PAUSED,
                     resolved_goal_id="goal-1", resolved_goal_status=GoalStatus.ACTIVE, match_count=1)
    defaults.update(overrides)
    return StateChangeCandidate(**defaults)


def _mission_change(**overrides) -> StateChangeCandidate:
    defaults = dict(operation=StateChangeOperation.SET_MISSION, raw_user_text="change my mission to X",
                     mission_title="Grow", mission_statement="Become the best version of myself.")
    defaults.update(overrides)
    return StateChangeCandidate(**defaults)


def test_create_goal_with_type_executes():
    d = decide(_create())
    assert d.outcome is StateChangePolicyOutcome.EXECUTE


def test_create_goal_without_type_asks_clarify():
    d = decide(_create(type=None))
    assert d.outcome is StateChangePolicyOutcome.ASK_CLARIFY


def test_unambiguous_valid_transition_executes():
    d = decide(_status_change())  # Active -> Paused, allowed
    assert d.outcome is StateChangePolicyOutcome.EXECUTE


def test_zero_matches_asks_clarify():
    d = decide(_status_change(resolved_goal_id=None, resolved_goal_status=None, match_count=0))
    assert d.outcome is StateChangePolicyOutcome.ASK_CLARIFY


def test_multiple_matches_asks_clarify():
    d = decide(_status_change(resolved_goal_id=None, match_count=2))
    assert d.outcome is StateChangePolicyOutcome.ASK_CLARIFY


def test_invalid_transition_rejected():
    # Completed -> Paused is not in ALLOWED_TRANSITIONS
    d = decide(_status_change(resolved_goal_status=GoalStatus.COMPLETED, new_status=GoalStatus.PAUSED))
    assert d.outcome is StateChangePolicyOutcome.REJECT_INVALID_TRANSITION


def test_cancelling_a_goal_asks_confirmation_even_when_unambiguous_and_valid():
    d = decide(_status_change(new_status=GoalStatus.CANCELLED))
    assert d.outcome is StateChangePolicyOutcome.ASK_CONFIRMATION


def test_archiving_a_goal_asks_confirmation():
    d = decide(_status_change(resolved_goal_status=GoalStatus.COMPLETED, new_status=GoalStatus.ARCHIVED))
    assert d.outcome is StateChangePolicyOutcome.ASK_CONFIRMATION


def test_completing_a_goal_executes_not_confirms():
    # Completed is deliberately NOT in the high-risk set (still has an
    # outgoing edge to Archived) — see policy.py's module docstring.
    d = decide(_status_change(new_status=GoalStatus.COMPLETED))
    assert d.outcome is StateChangePolicyOutcome.EXECUTE


def test_mission_change_with_both_fields_asks_confirmation():
    d = decide(_mission_change())
    assert d.outcome is StateChangePolicyOutcome.ASK_CONFIRMATION


def test_mission_change_missing_statement_asks_clarify_not_confirmation():
    d = decide(_mission_change(mission_statement=None))
    assert d.outcome is StateChangePolicyOutcome.ASK_CLARIFY


def test_mission_change_missing_title_asks_clarify():
    d = decide(_mission_change(mission_title=None))
    assert d.outcome is StateChangePolicyOutcome.ASK_CLARIFY
