"""V1-M2 §"do not design this from scratch" — direct structural copy of
memory/extraction.py's pattern: an LLM call proposes a candidate as
strict JSON, fully re-validated in Python before a StateChangeCandidate
is even constructed, fail-closed to None on anything malformed. The LLM
never writes anything and never resolves an entity to a real id itself.

Deliberately extracts AT MOST ONE operation per message, unlike memory's
multi-candidate extraction. A goal/mission state change is a single
explicit instruction ("pause my goal", "create a goal to X") — real
dogfooding of memory extraction (2026-08-15, see its module docstring)
showed multi-candidate extraction is the right call for a dense
statement of several distinct FACTS, but there's no equivalent case
here: a message asking for two different state changes at once is rare
enough, and the ambiguity/mis-resolution risk of parsing it high enough,
that this first pass keeps the shape simple. Worth revisiting if real
usage shows otherwise (same spirit as this project's other "don't build
what isn't demonstrated yet" calls).

Entity resolution (goal_ref -> a real Goal id) happens here, right after
validation, deterministic Python only — exact case-insensitive match
against the SAME known_goals list the prompt was shown, per the
2026-08-16 known-memories lesson (ARCHITECTURE_ISSUES.md): give the
extraction step real current titles as context so it references an
existing one rather than inventing/fuzzy-matching after the fact. No
fuzzy matching here either — an inexact match is exactly the "ambiguous
reference" case state_change/policy.py's ASK_CLARIFY outcome exists for,
never a guess.

2026-08-27 addition: also takes the session's recent conversation
turns as context, same _format_recent_context text the reply prompts
already use. Found in real dogfooding: ASK_CLARIFY's whole design
assumed a follow-up like "a Project" would be re-sent as a full,
self-contained command ("if the user retypes more specifically, that
next message just goes through this same pipeline again" — the
original design note in ARCHITECTURE_ISSUES.md's 2026-08-25 entry).
That's not how anyone actually replies to a question — a bare "a
Project" carries no title at all in isolation, so extraction needs the
immediately preceding exchange (Jarvis's clarifying question + the
user's original command) to reconstruct the full instruction. Caller
passes recent_context; a None/absent value degrades gracefully to the
original single-message behavior.
"""
from __future__ import annotations

from typing import Any, Optional

from contracts.enums import GoalStatus, GoalType
from contracts.state_change_candidate import StateChangeCandidate, StateChangeOperation
from infra.llm_router import complete_json
from infra.logging import get_logger

log = get_logger("state_change.extraction")

_VALID_OPS = {op.value for op in StateChangeOperation}
_VALID_STATUSES = {s.value for s in GoalStatus}
_VALID_TYPES = {t.value for t in GoalType}
_MAX_KNOWN_GOALS_SHOWN = 30  # keep the prompt bounded regardless of how many goals exist

_EXTRACTION_SYSTEM = (
    "You read one user message for a personal AI system that manages "
    "Goals and a Mission. Decide whether the message is an EXPLICIT "
    "INSTRUCTION to create a goal, change a goal's status, or change "
    "the Mission — never infer this from mood, venting, or "
    "conversational drift. \"I don't feel like building Jarvis right "
    "now\" is venting, NOT an instruction to pause anything. If the "
    "message is not a clear, explicit instruction to do one of these "
    "three things, operation must be null.\n\n"
    "For set_goal_status: goal_ref must be copied VERBATIM, character-"
    "for-character, from the list of existing goal titles given below "
    "— never invent a title, never paraphrase one, never guess which "
    "one the user means. If no listed title clearly matches, leave "
    "goal_ref null (this is treated as no goal found, not a guess). "
    "new_status must be exactly one of Draft, Active, Paused, "
    "Completed, Archived, Cancelled — map the user's wording naturally "
    "(pause -> Paused, finish/mark done -> Completed, cancel/drop -> "
    "Cancelled, resume/restart -> Active, archive -> Archived) but "
    "never guess a status the message doesn't clearly imply.\n\n"
    "For create_goal: title is the new goal's title in the user's own "
    "words, kept short. type is exactly one of LifeGoal, Project, Task, "
    "Habit, or null if genuinely unclear from the message (a caller "
    "decides what to do with null — you must not force a guess).\n\n"
    "For set_mission: mission_title and mission_statement are what the "
    "user actually stated as the new title/statement — never invent "
    "wording they didn't give you. Leave either null if the user didn't "
    "state it (the caller will ask for whatever's missing).\n\n"
    "2026-08-27 addition — continuing a clarifying question: you are "
    "also shown recent conversation from this session. If Jarvis's most "
    "recent message in that history was ITSELF a clarifying question "
    "about a Goal/Mission command (e.g. asking what type a new goal "
    "should be, or which existing goal was meant), and the user's "
    "CURRENT message answers that question — even a short answer like "
    "\"a Project\" or \"the second one\" with no title or goal name "
    "repeated in it — resolve the FULL original command by combining "
    "what the user asked for in that earlier turn with this answer. Do "
    "NOT treat a short answer to Jarvis's own question as an incomplete "
    "standalone command missing a title; carry the title/goal_ref over "
    "from the earlier turn in that same exchange. If the recent "
    "conversation doesn't show Jarvis asking a Goal/Mission clarifying "
    "question, treat the current message as a normal, standalone "
    "instruction as described above.\n\n"
    'Respond ONLY with JSON: {"operation": '
    '"create_goal"|"set_goal_status"|"set_mission"|null, '
    '"goal_ref": string|null, '
    '"new_status": "Draft"|"Active"|"Paused"|"Completed"|"Archived"|"Cancelled"|null, '
    '"title": string|null, '
    '"type": "LifeGoal"|"Project"|"Task"|"Habit"|null, '
    '"mission_title": string|null, "mission_statement": string|null, '
    '"confidence": number 0-1}.'
)


def _build_prompt(
    user_text: str,
    known_goals: list[tuple[str, str, str]],
    active_mission_title: Optional[str],
    recent_context: Optional[str] = None,
) -> str:
    goal_listing = "\n".join(
        f"- {title} (status: {status})" for title, _id, status in known_goals[:_MAX_KNOWN_GOALS_SHOWN]
    ) or "(no goals exist yet)"
    mission_line = active_mission_title or "(no Mission set)"
    recent_block = (
        f"Recent conversation this session (check whether Jarvis's last "
        f"message here was itself a clarifying question about a "
        f"Goal/Mission command — see instructions above for what to do "
        f"if so):\n{recent_context}\n\n"
        if recent_context else ""
    )
    return (
        f"Current Mission: {mission_line}\n\n"
        f"Existing goal titles (goal_ref must be copied verbatim from "
        f"this list, or left null):\n{goal_listing}\n\n"
        f"{recent_block}"
        f"User's message: {user_text}"
    )


def _validate(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Fail-closed re-validation of the whole payload, same shape as
    memory/extraction.py's _validate_one — every field checked against
    the exact allowed set before anything downstream trusts it."""
    if not isinstance(raw, dict):
        return None
    op = raw.get("operation")
    if op is None:
        return None
    if op not in _VALID_OPS:
        return None

    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        confidence = 0.7

    if op == StateChangeOperation.CREATE_GOAL.value:
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            return None
        type_raw = raw.get("type")
        if type_raw == "":
            # Cheap/small models often emit "" instead of a literal JSON
            # null for an omitted optional field — treat it the same as
            # None rather than failing the whole candidate closed over a
            # formatting quirk. A genuinely invalid non-empty string is
            # still rejected below.
            type_raw = None
        if type_raw is not None and type_raw not in _VALID_TYPES:
            return None
        return {"operation": op, "title": title.strip(), "type": type_raw, "confidence": confidence}

    if op == StateChangeOperation.SET_GOAL_STATUS.value:
        goal_ref = raw.get("goal_ref")
        new_status = raw.get("new_status")
        if not isinstance(goal_ref, str) or not goal_ref.strip():
            return None
        if new_status not in _VALID_STATUSES:
            return None
        return {"operation": op, "goal_ref": goal_ref.strip(), "new_status": new_status, "confidence": confidence}

    if op == StateChangeOperation.SET_MISSION.value:
        mission_title = raw.get("mission_title")
        mission_statement = raw.get("mission_statement")
        mission_title = mission_title.strip() if isinstance(mission_title, str) and mission_title.strip() else None
        mission_statement = (
            mission_statement.strip() if isinstance(mission_statement, str) and mission_statement.strip() else None
        )
        return {
            "operation": op, "mission_title": mission_title,
            "mission_statement": mission_statement, "confidence": confidence,
        }

    return None  # unreachable given the _VALID_OPS check above; kept as an explicit backstop


def resolve_goal_ref(goal_ref: str, known_goals: list[tuple[str, str, str]]) -> tuple[list[str], Optional[str]]:
    """Exact, case-insensitive match only — no fuzzy matching (see
    module docstring). Returns (matched_ids, status_of_the_single_match
    if exactly one).

    Made public (V1-M4) so capability_invocation/extraction.py can reuse
    this exact entity-resolution logic unchanged rather than duplicating
    it — same goal_ref shape, same "exact match or ask" discipline."""
    matches = [(gid, status) for title, gid, status in known_goals if title.strip().lower() == goal_ref.lower()]
    ids = [gid for gid, _status in matches]
    status = matches[0][1] if len(matches) == 1 else None
    return ids, status


async def extract_candidate(
    user_text: str,
    known_goals: Optional[list[tuple[str, str, str]]] = None,
    active_mission_title: Optional[str] = None,
    recent_context: Optional[str] = None,
) -> Optional[StateChangeCandidate]:
    known_goals = known_goals or []
    raw = await complete_json(
        system=_EXTRACTION_SYSTEM,
        prompt=_build_prompt(user_text, known_goals, active_mission_title, recent_context),
        task_type="fast", max_tokens=400,
    )
    if raw is None:
        log.info("State-change extraction unavailable/malformed — no candidate")
        return None

    validated = _validate(raw)
    if validated is None:
        if raw.get("operation") is not None:
            log.info(f"State-change extraction dropped an invalid payload, discarding: {raw!r}")
        return None

    op = StateChangeOperation(validated["operation"])

    if op is StateChangeOperation.CREATE_GOAL:
        return StateChangeCandidate(
            operation=op, raw_user_text=user_text, confidence=validated["confidence"],
            title=validated["title"],
            type=GoalType(validated["type"]) if validated["type"] else None,
        )

    if op is StateChangeOperation.SET_GOAL_STATUS:
        matched_ids, matched_status = resolve_goal_ref(validated["goal_ref"], known_goals)
        return StateChangeCandidate(
            operation=op, raw_user_text=user_text, confidence=validated["confidence"],
            goal_ref=validated["goal_ref"],
            new_status=GoalStatus(validated["new_status"]),
            resolved_goal_id=matched_ids[0] if len(matched_ids) == 1 else None,
            resolved_goal_status=GoalStatus(matched_status) if matched_status else None,
            match_count=len(matched_ids),
        )

    return StateChangeCandidate(  # SET_MISSION
        operation=op, raw_user_text=user_text, confidence=validated["confidence"],
        mission_title=validated["mission_title"],
        mission_statement=validated["mission_statement"],
    )
