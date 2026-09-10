"""
Orchestrator API (§7; §9 M6). OR-01: coordinates every request; does not
reason or make user-facing decisions itself — it sequences the other
subsystems' public APIs, exactly per §2's pipeline:

User Request -> Orchestrator -> Reasoning -> Planner -> Permission Check
-> Action Engine -> Capability -> Result

No Workflow contract exists in canonical §6 (see ARCHITECTURE_ISSUES.md)
so WorkflowResult here is a plain in-memory summary, not a persisted
entity — traceability already exists via Decision/Plan/Action/
PermissionCheckResult IDs (§11's Definition of Done doesn't require more).

AV-01's confirmation philosophy is implemented at exactly this layer:
independent actions run automatically when granted; only the ones that
actually need confirmation cause a stop, and only for that action — the
rest of the plan still completes (OR-07: independent work isn't serialized).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

from action_engine.api import get_action, run_action
from capabilities.registry import get as get_capability
from contracts.capability_result import CapabilityResult
from contracts.decision import Decision
from contracts.enums import PermissionStatus
from contracts.permission_check_result import PermissionCheckResult
from contracts.plan import Plan
from infra.logging import get_logger
from orchestrator.intent import detect_intent
from permission.api import check_permission, confirm as confirm_pcr, get_permission_check, reject as reject_pcr
from planner.api import approve_plan, create_plan_from_decision
from reasoning.api import reason

log = get_logger("orchestrator.api")


@dataclass
class WorkflowResult:
    intent: str
    decision: Decision
    plan: Optional[Plan] = None
    completed: list[CapabilityResult] = field(default_factory=list)
    awaiting_confirmation: list[PermissionCheckResult] = field(default_factory=list)
    denied: list[PermissionCheckResult] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)


async def _handle_action(action_id: str) -> tuple[str, object]:
    """Permission Check -> (dispatch or hold). Returns a (bucket, payload)
    tuple so the caller can sort results without re-touching the DB."""
    action = await get_action(action_id)
    entry = get_capability(action.capability_id)
    if entry is None:
        return "failed", {"action_id": action_id, "reason": f"capability '{action.capability_id}' not registered"}

    capability, _ = entry
    try:
        pcr = await check_permission(action_id, capability)
    except Exception as e:  # noqa: BLE001 — OR-06: never crash the workflow, record and continue
        return "failed", {"action_id": action_id, "reason": f"permission check error: {e}"}

    if pcr.permission_status == PermissionStatus.GRANTED:
        try:
            result = await run_action(action_id)
            return "completed", result
        except Exception as e:  # noqa: BLE001
            return "failed", {"action_id": action_id, "reason": f"execution error: {e}"}
    elif pcr.permission_status == PermissionStatus.CONFIRMATION_REQUIRED:
        return "awaiting_confirmation", pcr
    else:  # DENIED
        return "denied", pcr


async def run_request(user_text: str) -> WorkflowResult:
    intent = await detect_intent(user_text)
    decision = await reason(intent=intent, objective=user_text)
    log.info(f"Request '{user_text}' -> intent={intent}, decision={decision.summary}")

    if not decision.requires_plan:
        return WorkflowResult(intent=intent, decision=decision)

    plan = await create_plan_from_decision(decision)
    plan = await approve_plan(plan.id)  # AV-01: risk gating happens at Permission Check, not plan approval

    # OR-07: independent actions run concurrently, not serialized.
    outcomes = await asyncio.gather(*[_handle_action(aid) for aid in plan.action_ids])

    result = WorkflowResult(intent=intent, decision=decision, plan=plan)
    for bucket, payload in outcomes:
        getattr(result, bucket).append(payload)

    log.info(
        f"Plan {plan.id}: {len(result.completed)} completed, "
        f"{len(result.awaiting_confirmation)} awaiting confirmation, "
        f"{len(result.denied)} denied, {len(result.failed)} failed"
    )
    return result


async def resume_action(pcr_id: str, approved: bool):
    """AV-01's other half: continue a paused workflow once the user
    answers. Returns a CapabilityResult if approved+executed, or the
    rejected PermissionCheckResult otherwise."""
    if approved:
        await confirm_pcr(pcr_id)
        pcr = await get_permission_check(pcr_id)
        return await run_action(pcr.action_id)
    return await reject_pcr(pcr_id)
