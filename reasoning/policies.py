"""
Retrieval policies (CE-03) — reasoning/ M3.

Per architect guidance (2026-08-02): build the simplest correct pipeline
first, don't optimize retrieval yet. Two policies exist because those are
the only two intents with a real V0 data source to retrieve from — Goals
(M1) and Memory (M2). The implementation prompt suggested "Planning,
Programming" as first targets, but nothing backs Programming yet (no
repo/file capabilities until M5) — Reflection is the substitute here,
since it's equally grounded in what already exists.

A policy's only job (CE-01, CE-04): gather *candidates*. It does not
score or rank — that's reasoning/scoring.py. Each candidate carries just
enough raw information for scoring to work with, plus a `base_relevance`
hint that's a plain rule (status/importance lookup), not a computed score.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from contracts.enums import ContextSourceType, GoalStatus, Importance
from goals.api import list_goals
from memory.api import list_memories


@dataclass
class Candidate:
    source_type: ContextSourceType
    source_id: str
    payload: dict[str, Any]
    base_relevance: float  # simple rule-based starting point, 0-1
    confidence: float  # inherited from source, or 1.0 for direct facts
    updated_at: datetime
    reasoning: str  # why this candidate was pulled in, for CE-09 explainability


# Simple, fixed lookup tables — not a formula, not tuned. The whole point
# per architect guidance is that these are placeholders to measure
# against later, not a considered ranking model now.
_GOAL_STATUS_RELEVANCE = {
    GoalStatus.ACTIVE: 1.0,
    GoalStatus.PAUSED: 0.5,
    GoalStatus.DRAFT: 0.3,
}
_MEMORY_IMPORTANCE_RELEVANCE = {
    Importance.HIGH: 1.0,
    Importance.MEDIUM: 0.6,
    Importance.LOW: 0.3,
}


async def planning_policy(mission_id: str) -> list[Candidate]:
    """CE-03 Planning: 'Retrieve candidates from Goals, Calendar, Tasks,
    Current Mode, User Model, Energy.' V0 only has Goals and Memory built
    — Calendar/Tasks/Mode/UserModel/Energy don't exist yet, so this
    policy only pulls from what's real."""
    candidates: list[Candidate] = []

    goals = await list_goals(mission_id=mission_id)
    for g in goals:
        if g.status not in _GOAL_STATUS_RELEVANCE:
            continue  # Completed/Archived/Cancelled aren't planning-relevant
        candidates.append(
            Candidate(
                source_type=ContextSourceType.GOAL,
                source_id=g.id,
                payload=g.model_dump(mode="json"),
                base_relevance=_GOAL_STATUS_RELEVANCE[g.status],
                confidence=1.0,  # a Goal is a direct fact, not inferred
                updated_at=g.updated_at,
                reasoning=f"{g.type.value} '{g.title}' is {g.status.value}.",
            )
        )

    memories = await list_memories(status=None)
    for m in memories:
        if m.status.value != "Active":
            continue
        candidates.append(
            Candidate(
                source_type=ContextSourceType.MEMORY,
                source_id=m.id,
                payload=m.model_dump(mode="json"),
                base_relevance=_MEMORY_IMPORTANCE_RELEVANCE.get(m.importance, 0.5),
                confidence=m.confidence,
                updated_at=m.updated_at,
                reasoning=f"Active {m.type.value} memory, importance={m.importance.value}.",
            )
        )

    return candidates


async def reflection_policy(mission_id: str) -> list[Candidate]:
    """CE-03 Reflection: 'Retrieve candidates from Journal, Memories, Goal
    progress, Mood history, User Model.' Journal/Mood/UserModel don't
    exist yet — this pulls Goal progress (recently-changed goals,
    regardless of status, since reflection looks backward too) and all
    active Memories."""
    candidates: list[Candidate] = []

    goals = await list_goals(mission_id=mission_id)
    for g in goals:
        # Reflection cares about anything that changed recently, including
        # Completed/Cancelled — unlike planning_policy, which excludes them.
        candidates.append(
            Candidate(
                source_type=ContextSourceType.GOAL,
                source_id=g.id,
                payload=g.model_dump(mode="json"),
                base_relevance=0.8 if g.status in (GoalStatus.COMPLETED, GoalStatus.ACTIVE) else 0.4,
                confidence=1.0,
                updated_at=g.updated_at,
                reasoning=f"{g.type.value} '{g.title}' recently touched (status={g.status.value}).",
            )
        )

    memories = await list_memories(status=None)
    for m in memories:
        if m.status.value != "Active":
            continue
        candidates.append(
            Candidate(
                source_type=ContextSourceType.MEMORY,
                source_id=m.id,
                payload=m.model_dump(mode="json"),
                base_relevance=_MEMORY_IMPORTANCE_RELEVANCE.get(m.importance, 0.5),
                confidence=m.confidence,
                updated_at=m.updated_at,
                reasoning=f"Active {m.type.value} memory relevant to reflection.",
            )
        )

    return candidates


RETRIEVAL_POLICIES = {
    "Planning": planning_policy,
    "Reflection": reflection_policy,
}
