"""V1-M2: the deterministic policy boundary a StateChangeCandidate must
pass through before any Goal/Mission write happens — direct structural
copy of memory/candidate_policy.py's decide(). Nothing here calls an
LLM or touches the database; every branch is a plain check over fields
the candidate already carries (state_change/extraction.py did the only
I/O — a read-only known-goals fetch — before this ever runs).

Confirmation-risk tiers (finalized, see chat review before this module
was written):
- create_goal: low-risk, reversible -> EXECUTE, unless extraction left
  `type` null (create_goal has no default type to fall back on) -> ASK_CLARIFY.
- set_goal_status: unambiguous reference + valid transition + status
  not in the high-risk set below -> EXECUTE. Zero or >1 matching goal
  -> ASK_CLARIFY (an open question, not yes/no — distinct from memory's
  ASK_CONFIRMATION, which is why this module has its own outcome enum
  rather than reusing PolicyOutcome). Invalid transition (checked
  against goals.api.ALLOWED_TRANSITIONS, never reimplemented here) ->
  REJECT_INVALID_TRANSITION.
- Cancelled/Archived are terminal in goals.api.ALLOWED_TRANSITIONS (no
  outgoing transitions) — same "block/gate outright" precedent
  candidate_policy.py already set for BLOCK_SENSITIVE, applied to an
  irreversibility risk axis instead of a secrecy one. Deterministically
  checkable, so it belongs here, not in a prompt instruction. Completed
  is NOT in this set — it still has an outgoing edge to Archived, and
  marking something done isn't the same class of loss as cancelling it.
- set_mission: ALWAYS ASK_CONFIRMATION once both title and statement are
  present — highest-impact write in the system, referenced by name
  throughout Reasoning's grounding and every Decision's mission_alignment
  field. Either field missing -> ASK_CLARIFY first (confirmation is a
  yes/no gate, not a way to collect missing content).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from contracts.enums import GoalStatus
from contracts.state_change_candidate import StateChangeCandidate, StateChangeOperation
from goals.api import ALLOWED_TRANSITIONS

# Deterministic risk axis independent of ambiguity/validity — see
# module docstring. Completed is deliberately excluded.
_HIGH_RISK_STATUSES = {GoalStatus.CANCELLED, GoalStatus.ARCHIVED}


class StateChangePolicyOutcome(str, Enum):
    EXECUTE = "execute"                              # unambiguous, low-risk -> write immediately
    ASK_CONFIRMATION = "ask_confirmation"             # unambiguous but high-impact/high-risk -> y/N confirm
    ASK_CLARIFY = "ask_clarify"                       # zero or >1 goal match -> open question, not y/N
    REJECT_INVALID_TRANSITION = "reject_invalid_transition"  # goal found, transition not allowed


@dataclass
class StateChangePolicyDecision:
    outcome: StateChangePolicyOutcome
    reason: str  # human-readable — surfaced verbatim in the CLI prompt/reply


def decide(candidate: StateChangeCandidate) -> StateChangePolicyDecision:
    if candidate.operation is StateChangeOperation.CREATE_GOAL:
        if candidate.type is None:
            # goals.api.create_goal requires a GoalType — no default.
            # Guessing one silently would be exactly the kind of
            # invention extraction's own prompt is told never to do;
            # ask instead, same as an unresolved goal_ref.
            return StateChangePolicyDecision(
                StateChangePolicyOutcome.ASK_CLARIFY,
                "what kind of goal is this — a LifeGoal, Project, Task, or Habit?",
            )
        return StateChangePolicyDecision(StateChangePolicyOutcome.EXECUTE, "new goal, low-risk and reversible")

    if candidate.operation is StateChangeOperation.SET_GOAL_STATUS:
        if candidate.match_count == 0:
            return StateChangePolicyDecision(
                StateChangePolicyOutcome.ASK_CLARIFY,
                f"couldn't find a goal called \"{candidate.goal_ref}\" — "
                f"can you tell me the exact title?",
            )
        if candidate.match_count > 1:
            return StateChangePolicyDecision(
                StateChangePolicyOutcome.ASK_CLARIFY,
                f"\"{candidate.goal_ref}\" matches more than one goal — which one did you mean?",
            )

        current = candidate.resolved_goal_status
        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if candidate.new_status not in allowed:
            return StateChangePolicyDecision(
                StateChangePolicyOutcome.REJECT_INVALID_TRANSITION,
                f"can't move that goal from {current.value if current else '?'} to "
                f"{candidate.new_status.value} — allowed from {current.value if current else '?'}: "
                f"{sorted(s.value for s in allowed) or 'nothing (terminal status)'}",
            )

        if candidate.new_status in _HIGH_RISK_STATUSES:
            return StateChangePolicyDecision(
                StateChangePolicyOutcome.ASK_CONFIRMATION,
                f"about to mark that goal {candidate.new_status.value} — this can't be undone, confirm?",
            )

        return StateChangePolicyDecision(StateChangePolicyOutcome.EXECUTE, "unambiguous reference, valid transition")

    # SET_MISSION
    if candidate.mission_title is None or candidate.mission_statement is None:
        # Nothing to confirm yet — ASK_CONFIRMATION is a yes/no gate,
        # not a way to collect missing fields.
        return StateChangePolicyDecision(
            StateChangePolicyOutcome.ASK_CLARIFY,
            "what would you like the new Mission title and statement to be?",
        )
    return StateChangePolicyDecision(
        StateChangePolicyOutcome.ASK_CONFIRMATION,
        f"change your Mission to \"{candidate.mission_title}\" — "
        f"\"{candidate.mission_statement}\"? This is foundational, affecting every "
        f"goal and decision. Confirm?",
    )
