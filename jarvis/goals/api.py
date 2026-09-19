"""
Goal API (§7: public interface for the Goal system; §9 M1).

Per RFC-014, every other package talks to Goals only through this module.
This is also the ONLY writer of `goal` rows — every mutation goes through
`_apply_update`, which snapshots the pre-change state into `goal_history`
before writing (see infra/migrations/0003_goal_history.sql for why that
table exists and what it's standing in for).

Cross-record invariants enforced here (impossible to check on a single
Goal instance alone, which is why contracts/goal.py explicitly deferred
them to this module):
  - no dependency cycles (§6.2)
  - no circular parent relationships (§6.2)
  - every Goal traces to exactly one Mission, and cannot exist without an
    active one (§6.1 validation rule)
  - child goals inherit Mission from their ancestors (§6.2 example)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import asyncpg

from contracts.goal import Goal
from contracts.enums import GoalStatus, GoalType, Importance, Priority
from contracts.enums import MemoryType
from infra.logging import get_logger
from infra.storage import connection
from memory.api import remember_fact
from mission.api import get_active_mission

log = get_logger("goals.api")

# ARCHITECTURE ISSUE (see ARCHITECTURE_ISSUES.md, M1 entry): §6.2's
# lifecycle diagrams show Draft -> Active -> Paused -> Completed as one
# linear chain, plus separate Active -> Archived and Active -> Cancelled
# branches. Taken literally that leaves no way to resume from Paused, no
# way to abandon a Draft without ever activating it, and no way to
# archive a Completed goal for cleanup — all realistic operations. This
# transition table is my best-faith completion of the state machine,
# built from the documented paths plus the obvious symmetric/terminal
# cases. Flagged for architect review, not blocking.
# Public (V1-M2): state_change/policy.py needs to validate a proposed
# NL-driven transition against this exact table before ever calling
# set_status() below — per the handover doc's explicit instruction not
# to reinvent this validation. Single source of truth stays here, next
# to the function that enforces it; the policy layer imports it rather
# than keeping its own copy.
ALLOWED_TRANSITIONS: dict[GoalStatus, set[GoalStatus]] = {
    GoalStatus.DRAFT: {GoalStatus.ACTIVE, GoalStatus.ARCHIVED, GoalStatus.CANCELLED},
    GoalStatus.ACTIVE: {GoalStatus.PAUSED, GoalStatus.COMPLETED, GoalStatus.ARCHIVED, GoalStatus.CANCELLED},
    GoalStatus.PAUSED: {GoalStatus.ACTIVE, GoalStatus.ARCHIVED, GoalStatus.CANCELLED},
    GoalStatus.COMPLETED: {GoalStatus.ARCHIVED},
    GoalStatus.ARCHIVED: set(),
    GoalStatus.CANCELLED: set(),
}
_ALLOWED_TRANSITIONS = ALLOWED_TRANSITIONS  # internal alias, unchanged call sites below


class GoalNotFoundError(Exception):
    pass


class InvalidGoalTransitionError(Exception):
    pass


class DependencyCycleError(Exception):
    pass


class ParentCycleError(Exception):
    pass


class NoActiveMissionError(Exception):
    pass


def _row_to_goal(row) -> Goal:
    return Goal(
        id=str(row["id"]),
        type=GoalType(row["type"]),
        title=row["title"],
        description=row["description"],
        status=GoalStatus(row["status"]),
        priority=Priority(row["priority"]),
        parent_goal_id=str(row["parent_goal_id"]) if row["parent_goal_id"] else None,
        mission_id=str(row["mission_id"]),
        dependencies=json.loads(row["dependencies"]),
        success_metrics=json.loads(row["success_metrics"]),
        deadline=row["deadline"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        version=row["version"],
    )


async def get_goal(goal_id: str) -> Optional[Goal]:
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM goal WHERE id = $1;", goal_id)
        return _row_to_goal(row) if row else None


async def list_goals(
    status: Optional[GoalStatus] = None,
    mission_id: Optional[str] = None,
    parent_goal_id: Optional[str] = None,
) -> list[Goal]:
    query = "SELECT * FROM goal WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        params.append(status.value)
        query += f" AND status = ${len(params)}"
    if mission_id is not None:
        params.append(mission_id)
        query += f" AND mission_id = ${len(params)}"
    if parent_goal_id is not None:
        params.append(parent_goal_id)
        query += f" AND parent_goal_id = ${len(params)}"
    query += " ORDER BY created_at ASC;"

    async with connection() as conn:
        rows = await conn.fetch(query, *params)
        return [_row_to_goal(r) for r in rows]


async def _ancestor_ids(conn: asyncpg.Connection, goal_id: str) -> set[str]:
    """Walk parent_goal_id up to the root, returning every ancestor id."""
    ancestors: set[str] = set()
    current_id = goal_id
    while True:
        row = await conn.fetchrow("SELECT parent_goal_id FROM goal WHERE id = $1;", current_id)
        if row is None or row["parent_goal_id"] is None:
            break
        parent_id = str(row["parent_goal_id"])
        if parent_id in ancestors:
            break  # defensive: shouldn't happen if this module is the only writer
        ancestors.add(parent_id)
        current_id = parent_id
    return ancestors


async def _depends_on_transitively(conn: asyncpg.Connection, goal_id: str, target_id: str) -> bool:
    """True if goal_id (transitively, via its dependencies array) depends
    on target_id. Used to detect whether adding an edge would cycle."""
    visited: set[str] = set()
    stack = [goal_id]
    while stack:
        current = stack.pop()
        if current == target_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        row = await conn.fetchrow("SELECT dependencies FROM goal WHERE id = $1;", current)
        if row is None:
            continue
        stack.extend(json.loads(row["dependencies"]))
    return False


async def _snapshot_and_update(
    conn: asyncpg.Connection,
    current: Goal,
    new_fields: dict[str, Any],
    reason: str,
) -> Goal:
    """The one write path for every Goal mutation: snapshot current state
    into goal_history, then apply the update with version += 1. This is
    what makes 'Never overwrite. Archive previous versions. Store why they
    changed.' (Goals.md G-03) true without giving Goal a new id per
    version the way Mission does (see 0003_goal_history.sql docstring)."""
    now = datetime.now(timezone.utc)

    await conn.execute(
        """
        INSERT INTO goal_history (id, goal_id, version, snapshot, reason, changed_at)
        VALUES ($1, $2, $3, $4, $5, $6);
        """,
        str(uuid4()), current.id, current.version,
        current.model_dump_json(), reason, now,
    )

    merged = current.model_dump()
    merged.update(new_fields)
    merged["version"] = current.version + 1
    merged["updated_at"] = now

    # Re-validate the WHOLE proposed state through the pydantic contract
    # before writing anything — this is what catches things like
    # self-parenting or self-dependency for free, on every single update,
    # not just at creation.
    candidate = Goal(**merged)

    await conn.execute(
        """
        UPDATE goal SET
            title = $1, description = $2, status = $3, priority = $4,
            parent_goal_id = $5, dependencies = $6, success_metrics = $7,
            deadline = $8, updated_at = $9, completed_at = $10, version = $11
        WHERE id = $12;
        """,
        candidate.title, candidate.description, candidate.status.value,
        candidate.priority.value, candidate.parent_goal_id,
        json.dumps(candidate.dependencies), json.dumps(candidate.success_metrics),
        candidate.deadline, candidate.updated_at, candidate.completed_at,
        candidate.version, candidate.id,
    )
    return candidate


async def create_goal(
    type: GoalType,
    title: str,
    description: str = "",
    priority: Priority = Priority.MEDIUM,
    parent_goal_id: Optional[str] = None,
    dependencies: Optional[list[str]] = None,
    success_metrics: Optional[list[str]] = None,
    deadline: Optional[datetime] = None,
) -> Goal:
    now = datetime.now(timezone.utc)

    async with connection() as conn:
        async with conn.transaction():
            if parent_goal_id is not None:
                parent_row = await conn.fetchrow("SELECT * FROM goal WHERE id = $1;", parent_goal_id)
                if parent_row is None:
                    raise GoalNotFoundError(f"parent_goal_id {parent_goal_id} does not exist")
                mission_id = str(parent_row["mission_id"])
            else:
                active_mission = await get_active_mission()
                if active_mission is None:
                    raise NoActiveMissionError(
                        "Cannot create a Goal without an active Mission (§6.1 validation rule)."
                    )
                mission_id = active_mission.identity_id

            new_goal = Goal(
                type=type,
                title=title,
                description=description,
                status=GoalStatus.DRAFT,
                priority=priority,
                parent_goal_id=parent_goal_id,
                mission_id=mission_id,
                dependencies=dependencies or [],
                success_metrics=success_metrics or [],
                deadline=deadline,
                created_at=now,
                updated_at=now,
                completed_at=None,
                version=1,
            )

            for dep_id in new_goal.dependencies:
                dep_row = await conn.fetchrow("SELECT id FROM goal WHERE id = $1;", dep_id)
                if dep_row is None:
                    raise GoalNotFoundError(f"dependency {dep_id} does not exist")

            await conn.execute(
                """
                INSERT INTO goal
                    (id, type, title, description, status, priority, parent_goal_id,
                     mission_id, dependencies, success_metrics, deadline,
                     created_at, updated_at, completed_at, version)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15);
                """,
                new_goal.id, new_goal.type.value, new_goal.title, new_goal.description,
                new_goal.status.value, new_goal.priority.value, new_goal.parent_goal_id,
                new_goal.mission_id, json.dumps(new_goal.dependencies),
                json.dumps(new_goal.success_metrics), new_goal.deadline,
                new_goal.created_at, new_goal.updated_at, new_goal.completed_at,
                new_goal.version,
            )
    log.info(f"Created Goal '{new_goal.title}' ({new_goal.id}), type={new_goal.type.value}")
    return new_goal


async def update_goal(
    goal_id: str,
    reason: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    priority: Optional[Priority] = None,
    deadline: Optional[datetime] = None,
    success_metrics: Optional[list[str]] = None,
) -> Goal:
    """Generic field update — status, parent, and dependency changes go
    through the dedicated functions below since those carry extra
    invariant checks (transition validity, cycle detection)."""
    async with connection() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM goal WHERE id = $1 FOR UPDATE;", goal_id)
            if row is None:
                raise GoalNotFoundError(f"Goal {goal_id} does not exist")
            current = _row_to_goal(row)

            new_fields: dict[str, Any] = {}
            if title is not None:
                new_fields["title"] = title
            if description is not None:
                new_fields["description"] = description
            if priority is not None:
                new_fields["priority"] = priority
            if deadline is not None:
                new_fields["deadline"] = deadline
            if success_metrics is not None:
                new_fields["success_metrics"] = success_metrics

            updated = await _snapshot_and_update(conn, current, new_fields, reason)
    log.info(f"Updated Goal {goal_id}: {reason}")
    return updated


async def set_status(goal_id: str, new_status: GoalStatus, reason: str) -> Goal:
    async with connection() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM goal WHERE id = $1 FOR UPDATE;", goal_id)
            if row is None:
                raise GoalNotFoundError(f"Goal {goal_id} does not exist")
            current = _row_to_goal(row)

            if new_status not in _ALLOWED_TRANSITIONS[current.status]:
                raise InvalidGoalTransitionError(
                    f"Cannot transition Goal {goal_id} from {current.status.value} "
                    f"to {new_status.value}. Allowed from {current.status.value}: "
                    f"{sorted(s.value for s in _ALLOWED_TRANSITIONS[current.status])}"
                )

            new_fields: dict[str, Any] = {"status": new_status}
            if new_status == GoalStatus.COMPLETED:
                new_fields["completed_at"] = datetime.now(timezone.utc)

            updated = await _snapshot_and_update(conn, current, new_fields, reason)
    log.info(f"Goal {goal_id} status: {current.status.value} -> {new_status.value} ({reason})")

    # Deterministic memory trigger (§5.1 / M2, decision #10): the
    # Constitution's rule-based always-store list includes "long-term
    # goals" and "active projects" — the only two of its seven categories
    # with a real data source in V0 so far (see ARCHITECTURE_ISSUES.md,
    # M2 entry, for the other five). No AI judgment involved: this fires
    # unconditionally whenever a LifeGoal or Project becomes Active.
    if new_status == GoalStatus.ACTIVE and updated.type in (GoalType.LIFE_GOAL, GoalType.PROJECT):
        label = "Long-term goal" if updated.type == GoalType.LIFE_GOAL else "Active project"
        await remember_fact(
            type=MemoryType.FACT,
            title=f"{label}: {updated.title}",
            value=updated.description or updated.title,
            source_ids=[updated.id],
            related_goal_ids=[updated.id],
            importance=Importance.HIGH,
            confidence=1.0,
        )

    return updated


async def set_parent(goal_id: str, new_parent_id: Optional[str], reason: str) -> Goal:
    async with connection() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM goal WHERE id = $1 FOR UPDATE;", goal_id)
            if row is None:
                raise GoalNotFoundError(f"Goal {goal_id} does not exist")
            current = _row_to_goal(row)

            if new_parent_id is not None:
                parent_row = await conn.fetchrow("SELECT * FROM goal WHERE id = $1;", new_parent_id)
                if parent_row is None:
                    raise GoalNotFoundError(f"parent_goal_id {new_parent_id} does not exist")

                if new_parent_id == goal_id:
                    raise ParentCycleError("A Goal cannot be its own parent.")

                ancestors_of_new_parent = await _ancestor_ids(conn, new_parent_id)
                if goal_id in ancestors_of_new_parent or new_parent_id == goal_id:
                    raise ParentCycleError(
                        f"Setting {new_parent_id} as parent of {goal_id} would create a cycle "
                        f"({goal_id} is already an ancestor of {new_parent_id})."
                    )

                new_parent_mission_id = str(parent_row["mission_id"])
                if new_parent_mission_id != current.mission_id:
                    # Note (2026-08-07): unreachable via any current public
                    # API path — V0 has exactly one active Mission at a
                    # time (set_mission() always supersedes, never creates
                    # an independent second one), and Goal.mission_id now
                    # stores the stable identity that survives edits (see
                    # ARCHITECTURE_ISSUES.md), so two goals can no longer
                    # differ here just because one predates a Mission
                    # edit. Left in place as defensive/future-proofing —
                    # relevant if V0 ever gains real multi-mission support
                    # — not deleted, since it's still a correct invariant
                    # even though nothing exercises it today.
                    raise ValueError(
                        "Cannot reparent a Goal across Missions — child goals inherit Mission "
                        "from their ancestors (§6.2), and V0 does not support Mission migration."
                    )

            updated = await _snapshot_and_update(conn, current, {"parent_goal_id": new_parent_id}, reason)
    log.info(f"Goal {goal_id} reparented to {new_parent_id} ({reason})")
    return updated


async def add_dependency(goal_id: str, depends_on_id: str, reason: str) -> Goal:
    if goal_id == depends_on_id:
        raise DependencyCycleError("A Goal cannot depend on itself.")

    async with connection() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM goal WHERE id = $1 FOR UPDATE;", goal_id)
            if row is None:
                raise GoalNotFoundError(f"Goal {goal_id} does not exist")
            current = _row_to_goal(row)

            dep_row = await conn.fetchrow("SELECT id FROM goal WHERE id = $1;", depends_on_id)
            if dep_row is None:
                raise GoalNotFoundError(f"Goal {depends_on_id} does not exist")

            if depends_on_id in current.dependencies:
                return current  # idempotent no-op

            if await _depends_on_transitively(conn, depends_on_id, goal_id):
                raise DependencyCycleError(
                    f"Adding dependency {depends_on_id} to {goal_id} would create a cycle "
                    f"({depends_on_id} already transitively depends on {goal_id})."
                )

            new_deps = current.dependencies + [depends_on_id]
            updated = await _snapshot_and_update(conn, current, {"dependencies": new_deps}, reason)
    log.info(f"Goal {goal_id} now depends on {depends_on_id} ({reason})")
    return updated


async def remove_dependency(goal_id: str, depends_on_id: str, reason: str) -> Goal:
    async with connection() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM goal WHERE id = $1 FOR UPDATE;", goal_id)
            if row is None:
                raise GoalNotFoundError(f"Goal {goal_id} does not exist")
            current = _row_to_goal(row)

            new_deps = [d for d in current.dependencies if d != depends_on_id]
            updated = await _snapshot_and_update(conn, current, {"dependencies": new_deps}, reason)
    log.info(f"Goal {goal_id} no longer depends on {depends_on_id} ({reason})")
    return updated


async def get_goal_history(goal_id: str) -> list[dict]:
    """Every historical snapshot for a Goal, oldest first — the audit
    trail Goals.md G-03 asks for."""
    async with connection() as conn:
        rows = await conn.fetch(
            "SELECT version, snapshot, reason, changed_at FROM goal_history "
            "WHERE goal_id = $1 ORDER BY version ASC;",
            goal_id,
        )
        return [
            {
                "version": r["version"],
                "snapshot": json.loads(r["snapshot"]),
                "reason": r["reason"],
                "changed_at": r["changed_at"],
            }
            for r in rows
        ]


async def list_recent_goal_history(since: datetime) -> list[dict]:
    """Cross-goal, for insight/api.py's evidence gathering (M7) — owned
    here, not queried directly by insight/, per RFC-014."""
    async with connection() as conn:
        rows = await conn.fetch(
            "SELECT goal_id, version, snapshot, reason, changed_at FROM goal_history "
            "WHERE changed_at >= $1 ORDER BY changed_at ASC;",
            since,
        )
        return [
            {
                "goal_id": str(r["goal_id"]), "version": r["version"],
                "snapshot": json.loads(r["snapshot"]), "reason": r["reason"], "changed_at": r["changed_at"],
            }
            for r in rows
        ]
