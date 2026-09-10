"""
Action API (§7 action_engine/; §9 M4 subset — persistence + lifecycle
transitions only). Full execution (capability dispatch, retries) is M5's
"full Action lifecycle" deliverable; M4 needs Actions to exist as real,
traceable records so Planner can create them and Permission can check
them, per §11's traceability requirement.

Owns the `action` table exclusively (RFC-014).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from contracts.action import Action
from contracts.enums import ActionStatus, ActionType
from infra.logging import get_logger
from infra.storage import connection

log = get_logger("action_engine.api")

# AE-03's diagram + §6.7's fuller status enum, reconciled the same way
# goals/api.py and memory/api.py did (see ARCHITECTURE_ISSUES.md pattern).
_ALLOWED_TRANSITIONS: dict[ActionStatus, set[ActionStatus]] = {
    ActionStatus.PENDING: {ActionStatus.READY, ActionStatus.CANCELLED},
    ActionStatus.READY: {ActionStatus.RUNNING, ActionStatus.CANCELLED},
    ActionStatus.RUNNING: {ActionStatus.COMPLETED, ActionStatus.FAILED, ActionStatus.CANCELLED},
    ActionStatus.COMPLETED: set(),
    ActionStatus.FAILED: set(),
    ActionStatus.CANCELLED: set(),
}


class ActionNotFoundError(Exception):
    pass


class InvalidActionTransitionError(Exception):
    pass


def _row_to_action(row) -> Action:
    return Action(
        id=str(row["id"]),
        plan_id=str(row["plan_id"]),
        capability_id=row["capability_id"],
        type=ActionType(row["type"]),
        title=row["title"],
        parameters=json.loads(row["parameters"]),
        status=ActionStatus(row["status"]),
        permission_check_id=str(row["permission_check_id"]) if row["permission_check_id"] else None,
        retry_count=row["retry_count"],
        result_id=str(row["result_id"]) if row["result_id"] else None,
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


async def get_action(action_id: str) -> Optional[Action]:
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM action WHERE id = $1;", action_id)
        return _row_to_action(row) if row else None


async def create_action(
    plan_id: str, capability_id: str, type: ActionType, title: str,
    parameters: Optional[dict[str, Any]] = None,
) -> Action:
    new_action = Action(
        plan_id=plan_id, capability_id=capability_id, type=type, title=title,
        parameters=parameters or {}, status=ActionStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    async with connection() as conn:
        await conn.execute(
            """
            INSERT INTO action (id, plan_id, capability_id, type, title, parameters,
                                 status, permission_check_id, retry_count, result_id, created_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11);
            """,
            new_action.id, new_action.plan_id, new_action.capability_id, new_action.type.value,
            new_action.title, json.dumps(new_action.parameters), new_action.status.value,
            new_action.permission_check_id, new_action.retry_count, new_action.result_id,
            new_action.created_at,
        )
    return new_action


async def set_status(
    action_id: str, new_status: ActionStatus, permission_check_id: Optional[str] = None,
    started_at: Optional[datetime] = None, completed_at: Optional[datetime] = None,
    result_id: Optional[str] = None, retry_count: Optional[int] = None,
) -> Action:
    async with connection() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM action WHERE id = $1 FOR UPDATE;", action_id)
            if row is None:
                raise ActionNotFoundError(f"Action {action_id} does not exist")
            current = _row_to_action(row)

            if new_status not in _ALLOWED_TRANSITIONS[current.status]:
                raise InvalidActionTransitionError(
                    f"Cannot transition Action {action_id} from {current.status.value} to {new_status.value}"
                )

            pc_id = permission_check_id or current.permission_check_id
            # Re-validate through the contract — this is what enforces
            # "permission_check_id required once status leaves Pending"
            # (contracts/action.py, ARCHITECTURE_ISSUES.md M0 entry).
            candidate = Action(
                id=current.id, plan_id=current.plan_id, capability_id=current.capability_id,
                type=current.type, title=current.title, parameters=current.parameters,
                status=new_status, permission_check_id=pc_id,
                retry_count=retry_count if retry_count is not None else current.retry_count,
                result_id=result_id or current.result_id, created_at=current.created_at,
                started_at=started_at or current.started_at,
                completed_at=completed_at or current.completed_at,
            )

            await conn.execute(
                """UPDATE action SET status=$1, permission_check_id=$2, started_at=$3,
                   completed_at=$4, result_id=$5, retry_count=$6 WHERE id=$7;""",
                candidate.status.value, candidate.permission_check_id, candidate.started_at,
                candidate.completed_at, candidate.result_id, candidate.retry_count, action_id,
            )
    log.info(f"Action {action_id}: {current.status.value} -> {new_status.value}")
    return candidate


class PermissionNotSatisfiedError(Exception):
    pass


class CapabilityNotFoundError(Exception):
    pass


async def run_action(action_id: str, max_attempts: int = 2):
    """M5: real execution. Action must be Ready with a granted or
    confirmed permission check (§6.9's "may not transition past Ready
    until confirmation_status = confirmed"). AE-05: retries in-line, up to
    max_attempts, before giving up — no scheduler exists yet to retry
    across a background loop.

    Local import of permission.api below is deliberate: permission/api.py
    already imports action_engine.api (to move Actions out of Pending),
    so importing at module level here would create a circular import.
    Deferred import breaks the cycle safely.
    """
    from contracts.capability_result import CapabilityResult
    from contracts.enums import ConfirmationStatus, PermissionStatus
    from permission.api import get_permission_check
    from capabilities.registry import get as get_capability

    action = await get_action(action_id)
    if action is None:
        raise ActionNotFoundError(f"Action {action_id} does not exist")
    if action.status != ActionStatus.READY:
        raise InvalidActionTransitionError(f"Action {action_id} is not Ready (status={action.status.value})")

    pcr = await get_permission_check(action.permission_check_id)
    if pcr.permission_status == PermissionStatus.DENIED:
        raise PermissionNotSatisfiedError(f"Action {action_id}: permission denied")
    if pcr.permission_status == PermissionStatus.CONFIRMATION_REQUIRED and pcr.confirmation_status != ConfirmationStatus.CONFIRMED:
        raise PermissionNotSatisfiedError(f"Action {action_id}: awaiting user confirmation")

    entry = get_capability(action.capability_id)
    if entry is None:
        raise CapabilityNotFoundError(f"No registered capability '{action.capability_id}'")
    _capability, executor = entry

    await set_status(action_id, ActionStatus.RUNNING, started_at=datetime.now(timezone.utc))

    last_error: Optional[str] = None
    for attempt in range(max_attempts):
        try:
            output = await executor(action.parameters)
            result = CapabilityResult(action_id=action_id, success=True, output=output)
            await _persist_result(result)
            await set_status(
                action_id, ActionStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc), result_id=result.id, retry_count=attempt,
            )
            log.info(f"Action {action_id} completed via {action.capability_id}")
            return result
        except Exception as e:  # noqa: BLE001 — capability failures are data, not bugs, to the caller
            last_error = str(e)
            log.info(f"Action {action_id} attempt {attempt + 1}/{max_attempts} failed: {last_error}")

    result = CapabilityResult(action_id=action_id, success=False, error=last_error)
    await _persist_result(result)
    await set_status(
        action_id, ActionStatus.FAILED,
        completed_at=datetime.now(timezone.utc), result_id=result.id, retry_count=max_attempts - 1,
    )
    return result


async def _persist_result(result) -> None:
    async with connection() as conn:
        await conn.execute(
            """INSERT INTO capability_result
               (id, action_id, success, output, execution_time, tokens_used, credits_used, error, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9);""",
            result.id, result.action_id, result.success, json.dumps(result.output),
            result.execution_time, result.tokens_used, result.credits_used, result.error, result.created_at,
        )


async def list_actions(status: Optional[ActionStatus] = None) -> list[Action]:
    """For insight/api.py's evidence gathering (M7)."""
    if status is not None:
        query, params = "SELECT * FROM action WHERE status = $1 ORDER BY created_at ASC;", [status.value]
    else:
        query, params = "SELECT * FROM action ORDER BY created_at ASC;", []
    async with connection() as conn:
        rows = await conn.fetch(query, *params)
        return [_row_to_action(r) for r in rows]
