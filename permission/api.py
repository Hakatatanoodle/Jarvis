"""
Permission API (§7; §9 M4). The single gate (decision #1, §2) — nothing
executes without a PermissionCheckResult upstream of it.

check_permission() is the only writer of permission_check_result rows,
and the only path that moves an Action out of Pending (via
action_engine.api.set_status), enforcing §6.9: "the Action Engine may not
transition the Action past Ready until confirmation_status = confirmed."

Thresholds below are the simplest correct starting rule (Low/Medium ->
granted, High -> confirmation_required, Critical -> denied) — not tuned,
not configurable yet. Permission Profiles (Conservative/Balanced/
Autonomous, PS-06) are §5.2/M8 scope.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from action_engine.api import ActionNotFoundError, get_action
from action_engine.api import set_status as set_action_status
from contracts.capability import Capability
from contracts.enums import ActionStatus, ConfirmationStatus, PermissionStatus, RiskLevel
from contracts.permission_check_result import PermissionCheckResult
from infra.logging import get_logger
from infra.storage import connection
from permission.risk_calculator import compute_risk

log = get_logger("permission.api")

_RULE_NAME = "m4_default_threshold"


def _row_to_pcr(row) -> PermissionCheckResult:
    from contracts.permission_check_result import RiskFactors
    return PermissionCheckResult(
        id=str(row["id"]),
        capability_id=row["capability_id"],
        plan_id=str(row["plan_id"]) if row["plan_id"] else None,
        proposed_parameters=json.loads(row["proposed_parameters"]),
        baseline_risk=RiskLevel(row["baseline_risk"]),
        computed_risk=RiskLevel(row["computed_risk"]),
        risk_factors=RiskFactors(**json.loads(row["risk_factors"])),
        permission_status=PermissionStatus(row["permission_status"]),
        confirmation_status=ConfirmationStatus(row["confirmation_status"]),
        permission_rule_used=row["permission_rule_used"],
        action_id=str(row["action_id"]) if row["action_id"] else None,
        decided_at=row["decided_at"],
        resolved_at=row["resolved_at"],
    )


async def get_permission_check(pcr_id: str) -> Optional[PermissionCheckResult]:
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM permission_check_result WHERE id = $1;", pcr_id)
        return _row_to_pcr(row) if row else None


async def check_permission(action_id: str, capability: Capability) -> PermissionCheckResult:
    action = await get_action(action_id)
    if action is None:
        raise ActionNotFoundError(f"Action {action_id} does not exist")

    computed_risk, risk_factors = compute_risk(capability, action)

    if computed_risk in (RiskLevel.LOW, RiskLevel.MEDIUM):
        permission_status = PermissionStatus.GRANTED
        confirmation_status = ConfirmationStatus.NOT_APPLICABLE
        next_action_status = ActionStatus.READY
    elif computed_risk == RiskLevel.HIGH:
        permission_status = PermissionStatus.CONFIRMATION_REQUIRED
        confirmation_status = ConfirmationStatus.PENDING
        next_action_status = ActionStatus.READY  # reaches Ready; blocked before Running (§6.9)
    else:  # CRITICAL
        permission_status = PermissionStatus.DENIED
        confirmation_status = ConfirmationStatus.NOT_APPLICABLE
        next_action_status = ActionStatus.CANCELLED

    result = PermissionCheckResult(
        capability_id=capability.id,
        proposed_parameters=action.parameters,
        baseline_risk=capability.baseline_risk_level,
        computed_risk=computed_risk,
        risk_factors=risk_factors,
        permission_status=permission_status,
        confirmation_status=confirmation_status,
        permission_rule_used=_RULE_NAME,
        action_id=action.id,
        decided_at=datetime.now(timezone.utc),
    )

    async with connection() as conn:
        await conn.execute(
            """
            INSERT INTO permission_check_result
                (id, capability_id, plan_id, proposed_parameters, baseline_risk, computed_risk,
                 risk_factors, permission_status, confirmation_status, permission_rule_used,
                 action_id, decided_at, resolved_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13);
            """,
            result.id, result.capability_id, None, json.dumps(result.proposed_parameters),
            result.baseline_risk.value, result.computed_risk.value,
            result.risk_factors.model_dump_json(), result.permission_status.value,
            result.confirmation_status.value, result.permission_rule_used, result.action_id,
            result.decided_at, result.resolved_at,
        )

    await set_action_status(action_id, next_action_status, permission_check_id=result.id)
    log.info(f"Permission check for Action {action_id}: {permission_status.value} (risk={computed_risk.value})")
    return result


async def confirm(pcr_id: str) -> PermissionCheckResult:
    """User confirms a pending High-risk action. Moves confirmation_status
    to confirmed — this is what unblocks Action Engine (M5) from running
    past Ready, per §6.9."""
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM permission_check_result WHERE id = $1;", pcr_id)
        if row is None:
            raise ValueError(f"PermissionCheckResult {pcr_id} does not exist")
        current = _row_to_pcr(row)
        if current.confirmation_status != ConfirmationStatus.PENDING:
            raise ValueError(f"PermissionCheckResult {pcr_id} is not pending confirmation")

        now = datetime.now(timezone.utc)
        await conn.execute(
            "UPDATE permission_check_result SET confirmation_status = $1, resolved_at = $2 WHERE id = $3;",
            ConfirmationStatus.CONFIRMED.value, now, pcr_id,
        )
    log.info(f"PermissionCheckResult {pcr_id} confirmed by user")
    return await get_permission_check(pcr_id)


async def reject(pcr_id: str) -> PermissionCheckResult:
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM permission_check_result WHERE id = $1;", pcr_id)
        if row is None:
            raise ValueError(f"PermissionCheckResult {pcr_id} does not exist")
        current = _row_to_pcr(row)
        if current.confirmation_status != ConfirmationStatus.PENDING:
            raise ValueError(f"PermissionCheckResult {pcr_id} is not pending confirmation")

        now = datetime.now(timezone.utc)
        await conn.execute(
            "UPDATE permission_check_result SET confirmation_status = $1, resolved_at = $2 WHERE id = $3;",
            ConfirmationStatus.REJECTED.value, now, pcr_id,
        )
    if current.action_id:
        await set_action_status(current.action_id, ActionStatus.CANCELLED, permission_check_id=pcr_id)
    log.info(f"PermissionCheckResult {pcr_id} rejected by user")
    return await get_permission_check(pcr_id)
