"""
Planner API (§7; §9 M4). PL-01: Decision Engine decides *what*, Planner
decides *how*. "Flat task lists plus one level of decomposition" (§5.1)
— one Action per goal in the Decision, no dependency-graph solver.

Created Plans start in Draft (PL-10: Planner proposes, never silently
executes) — the user or Orchestrator approves before Permission Check
even runs.

capability_id below ("goals.advance") is a placeholder: no capability
registry exists until M5. This is the same kind of forward-reference
resolved for real once capabilities/registry.py exists.

RESOLVED in M5: capabilities/primitives/goal_ops.py registers a real
"goals.advance" capability; Actions created here now execute for real
via action_engine.api.run_action().
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from action_engine.api import create_action
from contracts.decision import Decision
from contracts.enums import ActionType, PlanStatus
from contracts.plan import Plan
from infra.logging import get_logger
from infra.storage import connection

log = get_logger("planner.api")

_PLACEHOLDER_CAPABILITY_ID = "goals.advance"


class DecisionDoesNotRequirePlanError(Exception):
    pass


def _row_to_plan(row) -> Plan:
    return Plan(
        id=str(row["id"]), decision_id=str(row["decision_id"]), title=row["title"],
        objective=row["objective"], status=PlanStatus(row["status"]),
        action_ids=json.loads(row["action_ids"]), estimated_duration=row["estimated_duration"],
        progress=row["progress"], version=row["version"],
    )


async def get_plan(plan_id: str) -> Optional[Plan]:
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM plan WHERE id = $1;", plan_id)
        return _row_to_plan(row) if row else None


async def create_plan_from_decision(decision: Decision) -> Plan:
    if not decision.requires_plan:
        raise DecisionDoesNotRequirePlanError(
            f"Decision {decision.id} has requires_plan=False; nothing to compile."
        )

    plan_id = str(uuid4())
    now = datetime.now(timezone.utc)

    # Plan row must exist first — Action has a FK on plan_id.
    async with connection() as conn:
        await conn.execute(
            """
            INSERT INTO plan (id, decision_id, title, objective, status, action_ids,
                               estimated_duration, progress, created_at, updated_at, version)
            VALUES ($1,$2,$3,$4,$5,'[]'::jsonb,$6,$7,$8,$8,$9);
            """,
            plan_id, decision.id, decision.summary, decision.objective,
            PlanStatus.DRAFT.value, "", 0, now, 1,
        )

    action_ids: list[str] = []
    for goal_id in decision.goal_ids:
        action = await create_action(
            plan_id=plan_id,
            capability_id=_PLACEHOLDER_CAPABILITY_ID,
            type=ActionType.COMPUTE,
            title=f"Advance goal {goal_id}",
            parameters={"goal_id": goal_id},
        )
        action_ids.append(action.id)

    new_plan = Plan(
        id=plan_id, decision_id=decision.id, title=decision.summary,
        objective=decision.objective, status=PlanStatus.DRAFT, action_ids=action_ids,
        estimated_duration="", progress=0, version=1,
    )

    async with connection() as conn:
        await conn.execute(
            "UPDATE plan SET action_ids = $1 WHERE id = $2;",
            json.dumps(action_ids), plan_id,
        )
    log.info(f"Created Plan '{new_plan.title}' ({new_plan.id}) with {len(action_ids)} action(s)")
    return new_plan


async def approve_plan(plan_id: str) -> Plan:
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM plan WHERE id = $1;", plan_id)
        if row is None:
            raise ValueError(f"Plan {plan_id} does not exist")
        current = _row_to_plan(row)
        if current.status != PlanStatus.DRAFT:
            raise ValueError(f"Plan {plan_id} is not in Draft (status={current.status.value})")

        now = datetime.now(timezone.utc)
        await conn.execute(
            "UPDATE plan SET status = $1, updated_at = $2 WHERE id = $3;",
            PlanStatus.APPROVED.value, now, plan_id,
        )
    log.info(f"Plan {plan_id} approved")
    return await get_plan(plan_id)
