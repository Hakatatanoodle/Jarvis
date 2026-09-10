"""V1-M4: turns a validated CapabilityInvocationCandidate into a real,
dispatched Action, or a clarify/relay note — orchestrator._handle_action
is reused verbatim for the GRANTED/CONFIRMATION_REQUIRED/DENIED branch
(direct template per the milestone brief); nothing here reimplements
risk or permission logic, that's check_permission()'s job untouched.

Why this module writes Decision+Plan rows at all (and isn't "reusing
the old intent/Decision/Plan system" the milestone brief says to avoid):
action(plan_id) and plan(decision_id) are both NOT NULL foreign keys
(migrations 0008/0009) — an Action cannot exist without them. This
writes the two minimal rows those FKs require via direct SQL, the same
way conversation.api._persist_grounded_decision already synthesizes a
Decision outside reasoning.reason() — it does NOT call detect_intent(),
reason(), or planner.api.create_plan_from_decision() (whose one-
action-per-goal shape is specific to the old goals.advance placeholder
and wrong for a general capability + validated-parameters shape).
intent="CapabilityInvocation" is a new free-text value, same trick
"Conversation" already uses on the same column (no CHECK constraint).

Plan is inserted directly as Approved (skipping a separate
approve_plan() call) — this Decision is never Draft-reviewable, it's a
direct consequence of an explicit user instruction, exactly like M2's
Goal/Mission writes never go through Plan review either.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from action_engine.api import create_action
from capabilities.registry import get as get_capability
from contracts.capability import Capability
from contracts.capability_invocation_candidate import CapabilityInvocationCandidate
from contracts.decision import Decision
from contracts.enums import ActionType, PlanStatus
from contracts.plan import Plan
from infra.logging import get_logger
from infra.storage import connection
from orchestrator.api import _handle_action

log = get_logger("capability_invocation.dispatch")

# Per-capability Action.type mapping — M5's calendar and M6's fs
# entries follow the same explicit-table approach goals.advance
# established (no generic inference rule; four data points isn't
# enough of a pattern yet either to generalize risk/type from).
_ACTION_TYPE_BY_CAPABILITY: dict[str, ActionType] = {
    "goals.advance": ActionType.COMPUTE,
    "fs.read": ActionType.READ,
    "fs.write": ActionType.WRITE,
    "calendar.read_events": ActionType.READ,
    "calendar.create_event": ActionType.WRITE,
}
_DEFAULT_ACTION_TYPE = ActionType.WRITE  # conservative default for any future capability not yet mapped

# V1-M6: session-scoped "current directory" for relative fs navigation
# ("go to Projects, then inside that find jarvis" across turns). Same
# module-level-global-for-the-single-process-session shape
# conversation.api.py already uses for _SESSION_ID — this system has no
# multi-tenant session table, one process is one session. Starts at the
# first allowed root (a sensible default "you are here") rather than
# None, so the very first relative reference in a session has somewhere
# to be relative to; None would just mean "no navigation happened yet"
# and force every caller to special-case it.
_FS_CAPABILITY_IDS = {"fs.read", "fs.write"}
_current_directory: Optional[str] = None


def get_current_directory() -> Optional[str]:
    """Read-only accessor for callers building the extraction prompt's
    current_directory context (conversation.api.py).

    Bug fix (dogfooding, 2026-08-31): this used to default to
    fs_ops._ALLOWED_ROOTS[0] (the config's first allow-listed root)
    unconditionally. That's wrong for what a person means by "this
    directory" / "the current repo" the first time they mention a
    filesystem thing in a session — they mean where they're actually
    running Jarvis from (os.getcwd()), not the top of whatever root
    happens to be listed first in config/default.yaml. Observed live:
    running from ~/Projects/Jarvis/jarvis-M6 and saying "read cli.py
    in the current repo" resolved against ~/Projects (the configured
    root) instead, looked for ~/Projects/cli.py, and failed with a
    File Not Found the person had no way to anticipate.

    Fixed default, in order: (1) the real process cwd, if it falls
    under an allowed root — this is what "current directory" actually
    means to someone who just launched the CLI from their project
    folder; (2) the first allowed root, same as before, only as a
    fallback for when the process wasn't even launched from inside an
    allowed root at all.
    """
    global _current_directory
    if _current_directory is None:
        from pathlib import Path
        from capabilities.primitives import fs_ops
        from capabilities.primitives._fs_safety import _is_descendant

        cwd = Path.cwd().resolve()
        if any(_is_descendant(cwd, root) for root in fs_ops._ALLOWED_ROOTS):
            _current_directory = str(cwd)
        elif fs_ops._ALLOWED_ROOTS:
            _current_directory = str(fs_ops._ALLOWED_ROOTS[0])
    return _current_directory


def _update_current_directory(resolved_path) -> None:
    global _current_directory
    _current_directory = str(resolved_path if resolved_path.is_dir() else resolved_path.parent)


@dataclass
class PendingCapabilityConfirmation:
    """V1-M4: returned instead of dispatching immediately when
    check_permission() (via orchestrator._handle_action) comes back
    CONFIRMATION_REQUIRED. Sibling of PendingStateChangeConfirmation/
    PendingMemoryConfirmation (same y/N UX in cli.py), but resolution
    goes through orchestrator.api.resume_action directly — that
    function already does confirm-or-reject + run, so this milestone
    doesn't need its own confirm/execute path the way M2/M3 did (they
    deliberately stayed off Permission/Action Engine; this milestone's
    whole point is routing through it)."""
    pcr_id: str
    reason: str


async def resolve_pending_capability_invocation(pending: PendingCapabilityConfirmation, approved: bool) -> Optional[str]:
    """Called back by the CLI (or any future frontend) after the user
    answers the confirmation prompt. Returns a short status line for
    display, or None if declined/failed."""
    from orchestrator.api import resume_action
    from contracts.capability_result import CapabilityResult

    outcome = await resume_action(pending.pcr_id, approved=approved)
    if not approved:
        return None
    if isinstance(outcome, CapabilityResult) and outcome.success:
        return f"Completed ({outcome.output})."
    if isinstance(outcome, CapabilityResult):
        return f"Failed: {outcome.error}"
    return None


async def _persist_decision_plan_action(
    candidate: CapabilityInvocationCandidate, capability: Capability, parameters: dict,
) -> str:
    """Writes the minimal Decision+Plan rows the Action FK chain
    requires (see module docstring), then a real Action row. Returns
    the new Action's id."""
    now = datetime.now(timezone.utc)
    goal_ids = [candidate.resolved_goal_id] if candidate.resolved_goal_id else []

    decision = Decision(
        objective=candidate.raw_user_text,
        summary=f"Invoke {capability.name}",
        reasoning=f"Natural-language capability invocation: {capability.id} ({capability.description}).",
        confidence=candidate.confidence,
        selected_option=capability.id,
        expected_outcome=f"{capability.name} runs with the extracted, validated parameters.",
        mission_alignment=1.0,
        goal_ids=goal_ids,
        context_item_ids=[],
        requires_plan=True,
    )
    async with connection() as conn:
        await conn.execute(
            """
            INSERT INTO decision
                (id, objective, summary, reasoning, confidence, alternatives,
                 selected_option, expected_outcome, risks, mission_alignment,
                 goal_ids, context_item_ids, requires_plan,
                 estimated_confirmation_needed, intent, created_at, version)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17);
            """,
            decision.id, decision.objective, decision.summary, decision.reasoning,
            decision.confidence, json.dumps(decision.alternatives), decision.selected_option,
            decision.expected_outcome, json.dumps(decision.risks), decision.mission_alignment,
            json.dumps(decision.goal_ids), json.dumps(decision.context_item_ids),
            decision.requires_plan, decision.estimated_confirmation_needed,
            "CapabilityInvocation", decision.created_at, decision.version,
        )

        plan_id = str(uuid4())
        await conn.execute(
            """
            INSERT INTO plan (id, decision_id, title, objective, status, action_ids,
                               estimated_duration, progress, created_at, updated_at, version)
            VALUES ($1,$2,$3,$4,$5,'[]'::jsonb,$6,$7,$8,$8,$9);
            """,
            plan_id, decision.id, decision.summary, decision.objective,
            PlanStatus.APPROVED.value, "", 0, now, 1,
        )

    action_type = _ACTION_TYPE_BY_CAPABILITY.get(capability.id, _DEFAULT_ACTION_TYPE)
    action = await create_action(
        plan_id=plan_id, capability_id=capability.id, type=action_type,
        title=f"{capability.name} (natural language)", parameters=parameters,
    )
    async with connection() as conn:
        await conn.execute("UPDATE plan SET action_ids = $1 WHERE id = $2;", json.dumps([action.id]), plan_id)
    return action.id


def _completed_detail(capability_id: str, output: Optional[dict]) -> str:
    """M5 (2026-09-06): real result data to actually hand the
    reply-generation model for the two calendar capabilities — the
    bare templated "Ran X." note gives it nothing to relay for a read
    (or confirm for a write), which is exactly the bug this fixes: a
    real, successful calendar.read_events call with zero of its actual
    data folded back in leaves the model's only honest answer "I don't
    have that," even though the Action Engine already fetched it
    moments earlier. Scoped to the two calendar capabilities only,
    matching this file's existing if-capability_id-in-(...) pattern
    elsewhere — not a generic "always summarize every capability's
    output" mechanism, since there's no second data point yet to know
    what a good general shape looks like. (fs.read's own version of
    this exact class of bug — found independently the same week during
    M6 dogfooding — is handled inline in the "completed" branch below,
    since its shape (directory listing vs. file content vs. token-
    budget truncation) didn't fit this function's signature cleanly;
    see V1_M6_IMPLEMENTATION_RECORD.md's "Dogfooding fix #1" / "#3".)
    """
    output = output or {}
    if capability_id == "calendar.read_events":
        events = output.get("events") or []
        if not events:
            return " No events found in that range."
        parts = []
        for e in events:
            start = e.get("start") or {}
            when = start.get("dateTime") or start.get("date") or str(start) or "?"
            parts.append(f"\"{e.get('summary', '(untitled)')}\" at {when}")
        return " Events found: " + "; ".join(parts) + "."
    if capability_id == "calendar.create_event":
        link = output.get("html_link")
        return f" Created: {link}" if link else " Created (no link returned)."
    return ""


async def resolve_and_dispatch(
    candidate: Optional[CapabilityInvocationCandidate],
) -> tuple[Optional[str], Optional[str], list[PendingCapabilityConfirmation]]:
    """V1-M4 pipeline: a validated candidate -> immediate execution, a
    pending y/N confirmation, or a relay note. Same (executed_note,
    relay_note, pending) contract as
    conversation.api._apply_state_change_policy, for identical wiring
    in conversation.api.handle().
    """
    if candidate is None:
        return None, None, []

    entry = get_capability(candidate.capability_id)
    if entry is None:
        # Extraction already validated against a live registry snapshot,
        # so this should be unreachable in practice — fail closed rather
        # than crash if the registry changed between extraction and here.
        log.warning(f"Capability '{candidate.capability_id}' vanished from the registry between extraction and dispatch")
        return None, None, []
    capability, _executor = entry

    # goals.advance's one parameter shape: requires an unambiguous goal
    # reference. Same ASK_CLARIFY discipline as
    # state_change/policy.py's set_goal_status branch — this is a
    # parameter-validity gate, not a risk decision (that's
    # check_permission's job, reached only once parameters are valid).
    if candidate.goal_ref is not None:
        if candidate.match_count == 0:
            return None, f"couldn't find a goal called \"{candidate.goal_ref}\" — can you tell me the exact title?", []
        if candidate.match_count > 1:
            return None, f"\"{candidate.goal_ref}\" matches more than one goal — which one did you mean?", []

    # V1-M6: filesystem path resolution + allow-list/blocklist
    # enforcement happens HERE, in Python, deterministically, before an
    # Action is even created — never trusted to what extraction
    # produced. resolved_path is real/absolute/symlink-resolved; the
    # confirmation prompt built below shows THIS, never candidate.path_ref
    # (the person's shorthand), per M6_HANDOVER_PROMPT.md's explicit
    # requirement.
    resolved_path = None
    if candidate.capability_id in _FS_CAPABILITY_IDS:
        if not candidate.path_ref:
            return None, "which file or folder did you mean?", []
        from capabilities.primitives import fs_ops
        from capabilities.primitives._fs_safety import PathBlocked, PathNotAllowed, resolve_and_authorize
        from pathlib import Path

        cwd = Path(get_current_directory() or "/")
        try:
            resolved_path = resolve_and_authorize(
                candidate.path_ref, cwd=cwd,
                allowed_roots=fs_ops._ALLOWED_ROOTS, blocked_patterns=fs_ops._BLOCKED_PATTERNS,
            )
        except PathNotAllowed:
            return None, f"\"{candidate.path_ref}\" is outside the folders I'm allowed to touch.", []
        except PathBlocked:
            return None, f"\"{candidate.path_ref}\" looks like a sensitive file — I won't touch that.", []

        if candidate.capability_id == "fs.write" and not candidate.content:
            return None, "what should I write to that file?", []

    # M5's calendar shape: account_ref is optional (omitting it is a
    # valid, meaningful choice — see calendar_ops.py), so only an
    # explicitly-given-but-unresolved account_ref is a clarify case,
    # same ambiguous/no-match split as goal_ref above. start/end are
    # required for either calendar capability — extraction.py's prompt
    # already tries hard to infer them, so a still-missing one here
    # means the model genuinely couldn't (an odd/ambiguous request),
    # not a bug to route around silently.
    if candidate.capability_id in ("calendar.read_events", "calendar.create_event"):
        if candidate.account_ref is not None:
            if candidate.account_match_count == 0:
                return None, f"I don't have a calendar account called \"{candidate.account_ref}\" connected — which one did you mean?", []
            if candidate.account_match_count > 1:
                return None, f"\"{candidate.account_ref}\" matches more than one connected account — which one did you mean?", []
        if candidate.start is None or candidate.end is None:
            return None, "what date/time range did you mean?", []
        if candidate.capability_id == "calendar.create_event" and not candidate.title:
            return None, "what should the event be called?", []

    # Generic-parameter mechanism (2026-09-07): any capability can
    # declare required extra_parameters (see contracts/capability.py's
    # ParamSpec docstring) without a new hardcoded clarify branch here
    # — this one loop replaces what would otherwise be a new
    # if-capability_id block per feature. Same ASK_CLARIFY shape as
    # every other gate in this function.
    for field_name, spec in capability.extra_parameters.items():
        if spec.required and field_name not in candidate.extra:
            question = spec.description.strip() or f"what should {field_name} be?"
            return None, question, []

    parameters: dict = {}
    if candidate.resolved_goal_id:
        parameters["goal_id"] = candidate.resolved_goal_id
    if candidate.note:
        parameters["note"] = candidate.note
    if resolved_path is not None:
        parameters["path"] = str(resolved_path)
        if candidate.capability_id == "fs.write":
            parameters["content"] = candidate.content
    if candidate.capability_id in ("calendar.read_events", "calendar.create_event"):
        parameters["start"] = candidate.start
        parameters["end"] = candidate.end
        if candidate.title:
            parameters["title"] = candidate.title
        if candidate.resolved_account:
            parameters["account"] = candidate.resolved_account
    # Generic-parameter mechanism: whatever extraction validated against
    # this capability's declared extra_parameters merges straight into
    # the Action's parameters — no per-field line needed here, unlike
    # the hardcoded blocks above it.
    parameters.update(candidate.extra)

    action_id = await _persist_decision_plan_action(candidate, capability, parameters)
    bucket, payload = await _handle_action(action_id)

    if bucket == "completed":
        result = payload  # CapabilityResult — orchestrator.api._handle_action's "completed" payload
        if resolved_path is not None:
            _update_current_directory(resolved_path)
            # V1-M6 bug fix (dogfooding, 2026-08-31): this branch used to
            # say only "Ran fs.read on X." and threw away result.output
            # entirely — the reply model was told an action succeeded but
            # given no actual file content/listing to relay, so it had
            # nothing to answer with and, observed live, hallucinated a
            # fake slash-command instead of just saying what was in the
            # directory. The grounding fact must carry the real data for
            # a read to be useful at all, not just a "yes it ran" note.
            output = getattr(result, "output", {}) or {}
            if output.get("is_directory"):
                entries = output.get("entries", [])
                listing = ", ".join(entries) if entries else "(empty)"
                return f"Ran fs.read on {resolved_path} — directory contents: {listing}.", None, []
            if "content" in output:
                content = output["content"]
                # Bug fix (dogfooding, 2026-08-31, round 3): this used
                # to cap at 3000 chars, which is fine for the DB/grounding
                # string itself but not for what happens next —
                # _conversational_reply (conversation/api.py, shared,
                # untouched) generates its actual reply with a hard
                # max_tokens=200. Telling the model "say so as something
                # you just did" about several KB of raw file content
                # invites it to quote/reproduce the file, which cannot
                # possibly fit in 200 tokens (~150 words) — observed
                # live, the reply cut off mid-docstring. The fix belongs
                # here, not in the shared 200-token cap (which plausibly
                # exists for good reason elsewhere): shrink what's
                # offered so an attempt to relay it in full actually
                # fits, and word it so the model describes/summarizes
                # rather than reaching for a verbatim quote.
                if len(content) > 600:
                    content = content[:600] + f"...[{len(output['content'])} chars total — describe/summarize, don't quote it all]"
                return (
                    f"Ran fs.read on {resolved_path} — describe what this file contains in your own words "
                    f"(don't quote it at length, the reply needs to stay short); a preview of its content: "
                    f"{content}"
                ), None, []
            return f"Ran {capability.name} on {resolved_path}.", None, []
        if candidate.capability_id in ("calendar.read_events", "calendar.create_event"):
            detail = _completed_detail(capability.id, getattr(result, "output", None))
            return f"Ran {capability.name}.{detail}", None, []
        target = f" on \"{candidate.goal_ref}\"" if candidate.goal_ref else ""
        return f"Ran {capability.name}{target}.", None, []

    if bucket == "awaiting_confirmation":
        pcr = payload
        target_desc = f" — target: {resolved_path}" if resolved_path is not None else ""
        relay = (
            f"still needs the user's yes/no confirmation, which will be "
            f"shown to them separately right after your reply, before "
            f"Jarvis proceeds — about to run {capability.name} "
            f"({capability.description}){target_desc} — confirm?"
        )
        prompt_target = f" ({resolved_path})" if resolved_path is not None else f" ({capability.description})"
        return None, relay, [PendingCapabilityConfirmation(pcr.id, f"run {capability.name}{prompt_target}? [y/N]")]

    if bucket == "denied":
        return None, f"can't run {capability.name} right now — it was denied by the permission check.", []

    # "failed"
    return None, f"tried to run {capability.name} but it failed: {payload.get('reason', 'unknown error')}.", []
