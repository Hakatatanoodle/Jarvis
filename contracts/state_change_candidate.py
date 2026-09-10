"""V1-M2: transient candidate for a natural-language Goal/Mission
state-change request ("pause my Build Jarvis goal", "create a goal to
run a marathon", "change my mission to X").

Own file, not contracts/enums.py — same reasoning contracts/memory_
candidate.py already gives for MemoryScope/Sensitivity: this shape is
transient (never persisted; goals/mission tables have no column for
it) and specific to one policy layer (state_change/policy.py), not
shared across every persisted §6 contract the way enums.py is.

StateChangeOperation is deliberately its own enum, not merged with
memory/candidate_policy.py's PolicyOutcome — different domain, different
outcome set (EXECUTE/ASK_CLARIFY/REJECT_INVALID_TRANSITION have no
memory equivalent), and merging them would couple two domains this
project keeps deliberately separate (single writer per domain).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from contracts.enums import GoalStatus, GoalType


class StateChangeOperation(str, Enum):
    CREATE_GOAL = "create_goal"
    SET_GOAL_STATUS = "set_goal_status"
    SET_MISSION = "set_mission"


@dataclass
class StateChangeCandidate:
    operation: StateChangeOperation
    raw_user_text: str
    confidence: float = 0.7

    # create_goal
    title: Optional[str] = None
    type: Optional[GoalType] = None

    # set_goal_status — goal_ref is the verbatim title extraction chose
    # from the known-goals list it was shown (never invented, never
    # fuzzy-matched by the LLM). Resolution against real Goal rows
    # happens in state_change/extraction.py, deterministic Python, and
    # is recorded here so policy.py can decide purely from this
    # candidate without touching the DB itself — same shape as
    # memory/candidate_policy.py taking a fully-formed MemoryCandidate.
    goal_ref: Optional[str] = None
    new_status: Optional[GoalStatus] = None
    resolved_goal_id: Optional[str] = None
    resolved_goal_status: Optional[GoalStatus] = None
    match_count: int = 0  # 0 = no match, 1 = resolved, >1 = ambiguous

    # set_mission
    mission_title: Optional[str] = None
    mission_statement: Optional[str] = None
