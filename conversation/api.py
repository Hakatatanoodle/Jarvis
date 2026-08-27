"""Conversation layer (V1-M1, V1_M1_IMPLEMENTATION_PLAN.md). Restores the
`Conversation` stage named in HOW_JARVIS_THINKS.md's original flow
(User -> Conversation -> Orchestrator -> ...) but never built in V0.

Exactly two jobs, no more (plan §1):
1. Mission gate — does an active Mission exist? If not, onboarding.
2. Routing — does this need Reasoning's grounded, evidence-first
   machinery, or is it ordinary conversation?

Revision (2026-08-2X, "make Reasoning actually conversational" — see
ARCHITECTURE_ISSUES.md): a question that needs grounding no longer goes
through orchestrator.api.run_request()'s rigid Planning/Reflection
intent classification, template Decision, and auto-fired Plan/Action
chain. It gets a real, free-form answer via _grounded_reply() below —
same evidence-gathering reasoning/api.py always used
(gather_grounded_evidence, reused directly, not reimplemented), same
non-negotiable honesty guards (never invent a goal relationship, never
conflate a Goal with the Mission, never misstate a status) — just no
longer forced into a fixed two-sentence template, and no longer
auto-touching every goal in the database on every casual question.
reasoning.api.reason() and orchestrator.api.run_request() are left
fully intact, still real and tested, for anything that later needs a
structured, action-triggering Decision — this module just doesn't call
them anymore for ordinary conversational questions.

Revision (V1-M2): the plan §9 non-goal above ("does NOT do natural-
language CRUD") was M1's deliberate deferral, not a permanent rule —
V1_PLANNING_INPUT.md §3 named it explicitly as an open question for a
later milestone. M2 is that milestone: limited NL CRUD for Goal
creation, Goal status changes, and Mission changes now lives in
state_change/extraction.py + state_change/policy.py, wired into
handle() the same way M3 wired in Memory — a cheap routing flag
(state_change_possible), a real extraction call only when that flag
says it's worth it, and a deterministic policy layer (never the LLM)
deciding execute/confirm/clarify. Reads of Goals/Mission for grounding
context remain read-only elsewhere in this module; the only writes
introduced by M2 go through goals.api.create_goal/set_status and
mission.api.set_mission — the same functions the CLI's /goal and
/mission commands already use.

Every turn — both routing paths — gets one row in the flat, append-only
ConversationTurn archive (plan §5/6). A grounded reply on the reasoning
path still gets persisted as a Decision (traceability/explainability,
PROJECT.md's "Jarvis should always be able to explain..."), but never
with requires_plan=True — no Plan or Action fires from ordinary
conversation anymore, matching what M3's Memory subsystem already does
(a statement is either worth keeping or it isn't; nothing about
discussing it should silently mutate other state).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from capabilities.registry import list_capabilities
from contracts.conversation_turn import ConversationTurn
from contracts.decision import Decision
from contracts.enums import ContextSourceType, MemoryStatus
from contracts.memory_candidate import MemoryCandidate
from contracts.state_change_candidate import StateChangeCandidate, StateChangeOperation
from goals.api import create_goal, list_goals
from goals.api import set_status as set_goal_status
from infra.llm_router import LLMUnavailableError, complete, complete_json
from infra.logging import get_logger
from infra.storage import connection
from memory.api import list_memories, record_taxonomy_gap, remember_from_candidate
from memory.candidate_policy import PolicyOutcome, decide as decide_memory_policy
from memory.extraction import extract_candidates
from mission.api import get_active_mission, set_mission
from reasoning.api import (
    _asserts_unsupported_relationship,
    _asserts_wrong_mission,
    _asserts_wrong_status,
    _looks_garbled,
    gather_grounded_evidence,
)
from state_change.extraction import extract_candidate as extract_state_change
from state_change.policy import StateChangePolicyOutcome, decide as decide_state_change_policy

log = get_logger("conversation.api")

# One id per process — this is the actual session boundary. Generated
# once at import time, so it's stable for the life of one `python3
# cli.py` run and different on every restart. This is what makes "gone
# after /exit" a real, enforced fact rather than an assumption (see
# contracts/conversation_turn.py's docstring for the full reasoning).
_SESSION_ID = str(uuid4())
_RECENT_TURNS_LIMIT = 6

_ONBOARDING_MESSAGE = (
    "Hey — I don't have a Mission set for you yet, so I can't help you plan "
    "or reflect on anything until I know what you're working toward.\n\n"
    "Run /mission set <title> | <statement> to get started, or /help to see "
    "everything I can do."
)

# §3: small, tight, unambiguous set — anything longer or less certain
# falls through to the LLM routing check instead of guessing here.
_GREETING_WORDS = {
    "hi", "hello", "hey", "yo", "sup", "thanks", "thank", "bye", "goodbye",
    "goodmorning", "goodnight", "morning", "night",
}
_GREETING_FILLER = {"good", "you", "jarvis", "there"}
_MAX_FAST_PATH_WORDS = 3

_ROUTING_SYSTEM = (
    "You make three independent judgments about a single user message for "
    "a personal AI system, in one pass — this call happens on every "
    "message, so both answers need to come from the same read.\n\n"
    "1. needs_reasoning: does this require Jarvis's goal/mission/memory-"
    "grounded reasoning to answer, or is it ordinary conversation? Say "
    "true ONLY if the message is actually asking about the user's real "
    "goals, mission, or progress — not just because it's phrased like "
    "'what should I do' or 'how do I'. This distinction matters: "
    "routing something to reasoning that shouldn't be there doesn't "
    "just give a bad answer, it spuriously touches the user's real goal "
    "history. Examples that need reasoning (true): 'what should I work "
    "on today', 'how am I doing on my goals', 'prioritize my tasks', "
    "'what's the status of my Test goal', 'what statuses can I move my "
    "Test goal to' — these ask about a specific, real goal, not about "
    "Jarvis in the abstract, even though 'what are the statuses I can "
    "set X to' superficially resembles a generic capability question. "
    "Examples that do NOT need reasoning (false), even though they're "
    "phrased as questions or requests: personal or relationship advice "
    "unrelated to goals ('I like this girl, what should I do'), "
    "GENERIC questions about Jarvis itself or how to use it with no "
    "specific goal/mission named ('how do I access X', 'what are your "
    "capabilities', 'what statuses exist' with no goal named), a direct "
    "question about what "
    "Jarvis knows/remembers about the user themselves ('what do you "
    "know about me', 'do you remember my name') — that's answered "
    "conversationally, straight from durable memory, NOT through goal-"
    "reasoning — greetings, small talk, opinions, banter.\n\n"
    "2. memory_candidate_possible: is there ANY realistic chance this "
    "message contains something worth durably remembering about the "
    "user — a name, a preference, a value, a life goal or aspiration, a "
    "fact about them, a standing instruction for how Jarvis should "
    "treat them? This is a cheap pre-filter, not the final decision — a "
    "separate, more careful pass makes the real call, so err toward "
    "true when genuinely unsure; a false here means that second pass "
    "never runs at all, so a wrong false silently loses real content. "
    "Say false only when the message is clearly just a question, a "
    "request for Jarvis to do something, small talk with nothing "
    "self-descriptive in it, or discussion of a topic unrelated to the "
    "user themselves.\n\n"
    "3. state_change_possible: is there ANY realistic chance this "
    "message is an explicit instruction to create a goal, change a "
    "goal's status (pause/resume/complete/cancel/archive), or change "
    "the Mission? Another, more careful pass makes the real call — say "
    "true whenever genuinely unsure, since a false here means that pass "
    "never runs and a real command is silently dropped. Also say true "
    "if you're shown recent conversation this session that ends with "
    "Jarvis asking a clarifying question about a Goal/Mission command "
    "(e.g. what type a new goal should be, or which existing goal was "
    "meant) and the current message could plausibly be answering it — "
    "even a short reply like \"a Project\" with no command language of "
    "its own; that later pass has the fuller context to actually "
    "resolve it. Say "
    "false for ordinary questions, small talk, or anything that isn't "
    "plausibly an instruction to change Goal/Mission state or an answer "
    "to Jarvis's own pending question about one.\n\n"
    'Respond ONLY with JSON: {"needs_reasoning": bool, '
    '"memory_candidate_possible": bool, "state_change_possible": bool}.'
)

_CONVERSATIONAL_SYSTEM = (
    "You are Jarvis, a personal AI operating system, responding in ordinary "
    "conversation — not a planning or reflection request. Be warm, brief, "
    "and personable. Respond with your final answer ONLY — never show a "
    "thinking process, numbered analysis, or step-by-step reasoning before "
    "your reply; the user should see the answer, not your scratchpad. You "
    "may reference the user's Mission title if it helps "
    "the reply feel grounded, but NEVER assert anything specific about the "
    "user's actual goals, memories, or progress — you don't have that "
    "evidence in front of you right now, and inventing it would be a "
    "hallucination. You will be told exactly what capabilities exist in "
    "this system and which of them you can use right now (usually none, in "
    "this reply) — NEVER claim to have saved, remembered, noted, "
    "scheduled, set, tracked, or done anything persistent, even if the "
    "user asks you to, unless you were explicitly told that capability is "
    "available to you right now. If you can't do something, say so "
    "plainly instead of pretending you did. You will also be shown recent "
    "turns from this same conversation session — you may naturally use "
    "anything the user told you earlier in this session (e.g. a name they "
    "asked you to use), but this context does not survive past this "
    "session, so don't imply it will (e.g. don't say 'I'll always "
    "remember that') UNLESS it is also listed in the separate 'What you "
    "know about the user' section — that section IS durable, persists "
    "across sessions, and you may say so plainly. Never claim something "
    "is remembered long-term unless it appears there or in a 'Memory "
    "action just taken this turn' line if one is given.\n\n"
    "SOURCE OF TRUTH: 'What you know about the user' is the ONLY "
    "authoritative source for facts about the user themselves (name, "
    "preferences, etc.). If 'Recent conversation this session' ever "
    "conflicts with it — including something YOU said earlier in this "
    "same session — the memory section wins, every time, no exception. "
    "Openly correct yourself if you said something wrong a moment ago; "
    "do not repeat or defend an earlier statement just because it's in "
    "the recent-conversation history. Specifically for the user's name: "
    "use ONLY a memory whose title is clearly about the USER's own name "
    "(e.g. \"user's name\") — never a memory about Jarvis's own name or "
    "identity (e.g. \"Jarvis's name\"), never something inferred from "
    "conversation flow, and never something you or the user said "
    "earlier this session unless it also matches that memory entry. If "
    "no such memory exists, say plainly that you don't know their name "
    "yet — do not guess or fall back on anything else that merely "
    "sounds like a name.\n\n"
    "IMPORTANT: when "
    "the user asks whether you remember or know something, check both "
    "the 'What you know about the user' and 'Recent conversation this "
    "session' sections first (applying the source-of-truth rule above "
    "if they conflict). If it's there, state it directly and "
    "confidently — do NOT reflexively say things like 'I don't have "
    "memory of our conversation' if the answer is literally shown to you "
    "above. Only say you don't know something if it genuinely isn't in "
    "the Mission, Capabilities, Memory, or Recent conversation sections "
    "you were given. Phrasing matters here: when recalling something "
    "already in the 'What you know about the user' list, use present-"
    "tense KNOWLEDGE language ('You're Yochan', 'I know you prefer X', "
    "'You mentioned...') — NOT persistence-action language like 'I've "
    "noted/saved/remembered this', which specifically implies something "
    "was just written to memory. Reserve that phrasing only for a "
    "'Memory action just taken this turn' line, if one is given — using "
    "it to describe something that was already known before this turn "
    "will incorrectly read as a fresh, unverified action claim. If the "
    "user's own name is known (per the source-of-truth rule above), "
    "address them by it naturally rather than a generic title like "
    "'boss' or 'man' — use the generic title only if they've explicitly "
    "said they prefer it, or their name genuinely isn't known yet. You "
    "will also be told the real interface this system has — NEVER refer "
    "to a UI element (tab, menu, button, dashboard, etc.) that isn't "
    "actually one of the listed slash commands; this is a terminal-only "
    "system with no graphical interface at all."
)
_FALLBACK_REPLY = "Hey — I'm here, just couldn't reach a language model for that reply. What can I help with?"
_CANNOT_PERSIST_REPLY = (
    "I want to be careful not to overstate what I've actually saved for "
    "you, so I won't claim that just now. I can still help with your "
    "goals, mission, and planning — try /goal, /mission, /memory, or "
    "just ask me what to work on."
)
_NO_GUI_REPLY = (
    "Just to be accurate — there's no menu or screen for that. This is a "
    "terminal-only system right now: /mission, /goal, /memory, /status, "
    "/decision, and /help are the whole interface."
)
_MALFORMED_REPLY_FALLBACK = "Sorry, that came out a bit garbled on my end — could you say that again?"

# Layer 2 (guarantee, not just reduce — same pattern as BUG-M6-01):
# for M1 the conversational path was side-effect-free by construction,
# so ANY first-person persistence claim was false, full stop. V1-M3
# changes that in exactly one case: a WRITE_DURABLE/BLOCK_SENSITIVE
# memory decision this same turn; V1-M2 adds a second: an EXECUTE'd
# Goal/Mission write (see _apply_memory_policy / _apply_state_change_policy
# and _conversational_reply's `memory_grounding` param — both funnel
# completed-write facts through the same param). The call site below
# only lifts the guard when that flag is set, so it still catches every
# other false persistence claim exactly as before — this isn't a
# loosening of the check itself, just a narrow, explicit exception for
# the cases where the claim is now actually true. A pending question or
# rejection (V1-M2's `relay_note`) is deliberately NOT part of this
# exception — it isn't a persistence claim, so the guard's normal
# behavior on whatever the model does with it is exactly what's wanted.
_ACTION_CLAIM_VERBS = {
    "note", "noted", "save", "saved", "remember", "remembered", "record",
    "recorded", "log", "logged", "add", "added", "set", "schedule",
    "scheduled", "remind", "reminded", "track", "tracked", "store",
    "stored", "keep", "kept", "update", "updated", "jot", "jotted",
}
_ACTION_CLAIM_SUBJECTS = re.compile(r"\b(i've|i have|i'll|i will|ive|i'm going to|im going to)\b")

# Found in dogfooding, 2026-08-10: the responder told the user their
# capabilities were "listed for you under the Mission tab" — a fully
# fabricated UI surface. This system has exactly one interface: this
# terminal and its slash commands. No tabs, menus, buttons, or screens
# exist anywhere. Different failure class from the action-claim guard
# above (that catches false persistence claims; this catches false
# claims about the product itself) — same "structured grounding +
# deterministic check" shape, applied to a different hallucination.
_REAL_INTERFACE = (
    "The ONLY interface that exists is this terminal, via slash commands: "
    "/mission, /goal, /memory, /status, /decision, /help. There is no "
    "GUI, no tabs, no menus, no buttons, no dashboard, no app, no website "
    "— nothing but this terminal. NEVER refer the user to a UI element "
    "that isn't one of these slash commands. This is also a PLAIN TEXT "
    "terminal — Markdown doesn't render here, so never use '#' headers, "
    "'**bold**', or horizontal rules ('---'); plain sentences only."
)
_FABRICATED_UI_TERMS = {
    "tab", "tabs", "menu", "button", "dashboard", "sidebar", "dropdown",
    "checkbox", "toggle", "settings page",
}


def _mentions_fabricated_ui(reply: str) -> bool:
    lowered = reply.lower()
    return any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in _FABRICATED_UI_TERMS)


# Found in dogfooding, 2026-08-10: a reply started with the literal line
# "Mission: I wanna be ironman" — the model pattern-matched the prompt's
# own "Label: value" formatting (see _conversational_reply's prompt
# construction) and echoed it back instead of just answering. This is a
# coherence/formatting failure, not a factual-grounding one — same
# category as reasoning/api.py's _looks_garbled, not the same category
# as the action-claim or fabricated-UI guards above. Checks for any line
# starting with one of the prompt's own literal field labels.
_PROMPT_LABELS = ("mission:", "capabilities:", "interface:", "recent conversation this session:", "user:")


def _leaks_prompt_structure(reply: str) -> bool:
    return any(line.strip().lower().startswith(_PROMPT_LABELS) for line in reply.splitlines())


# Found in dogfooding, 2026-08-14: openrouter/free (now the emergency
# fallback tried after every configured route candidate fails — see
# infra/llm_router.py's OpenRouterProvider and llm.emergency_fallback in
# config/default.yaml; at the time this was found it was still every
# task_type's primary) can select an underlying reasoning model, and that
# model's raw chain-of-thought leaked straight into the user-facing reply
# ("Here's a thinking process: 1. **Analyze User Input:** ..."). This
# looked structurally fixable via OpenRouter's documented
# reasoning.exclude request param (added to OpenRouterProvider.call), but
# OpenRouter's own docs state dynamic router models (openrouter/auto,
# openrouter/free) omit the reasoning field entirely — so that param is a
# no-op for this app's actual provider and the leak can't be suppressed
# request-side. Same category of bug as _leaks_prompt_structure just
# above: a deterministic, cheap check on the response, swapping in the
# existing generic fallback rather than showing the user a scratchpad.
# Kept regardless of which provider actually answered — this response-
# level filter is cheap insurance if any future candidate leaks similarly.
_REASONING_PREAMBLE_RE = re.compile(
    r"^\s*(here'?s (a|my) (thinking process|reasoning)\b|let'?s think\b|let me think\b)",
    re.IGNORECASE,
)
_REASONING_STEP_RE = re.compile(r"^\s*\d+\.\s*\*\*", re.MULTILINE)


def _leaks_reasoning_preamble(reply: str) -> bool:
    if _REASONING_PREAMBLE_RE.match(reply.strip()):
        return True
    # A numbered, bolded step list ("1.  **Analyze User Input:**") in the
    # first few lines is a strong, distinctive signal of a leaked
    # chain-of-thought scratchpad — genuine conversational replies don't
    # naturally take this shape.
    head = "\n".join(reply.splitlines()[:6])
    return bool(_REASONING_STEP_RE.search(head))


def _capability_grounding() -> str:
    caps = list_capabilities()
    if not caps:
        return "No capabilities are registered in this system at all right now."
    names = "; ".join(f"{c.id} ({c.description})" for c in caps)
    return (
        f"Capabilities that exist in this system: {names}. None of these "
        "are available to you in this conversational reply — you are "
        "generating text only, with no ability to call any capability, "
        "save anything, or take any action right now."
    )


def _claims_unbacked_action(reply: str) -> bool:
    lowered = reply.lower()
    if not _ACTION_CLAIM_SUBJECTS.search(lowered):
        return False
    return any(re.search(rf"\b{v}\b", lowered) for v in _ACTION_CLAIM_VERBS)


# Note (2026-08-09, deliberate, not an oversight): session-context below
# means the archive genuinely does carry "coco" into the next few turns
# now, so a claim like "I've noted that" is roughly true within this
# session's recent-turns window. This guard is kept strict anyway —
# unconditionally flagging any persistence claim — rather than adding a
# second heuristic to distinguish "session-scoped" from "permanent"
# claims. Reasoning: (a) the archive's recall is bounded, unstructured,
# and not something the user can rely on the way "I'll remember that"
# implies, so staying humble about it is still the more honest framing;
# (b) a session-vs-permanent distinction is exactly the kind of fragile,
# growing heuristic surface already rejected once this session for the
# routing design. The trade-off costs nothing functionally — recent
# turns still get included either way — it just means Jarvis describes
# its own ability more conservatively than it strictly needs to.


def _row_to_turn(row) -> ConversationTurn:
    return ConversationTurn(
        id=str(row["id"]), session_id=str(row["session_id"]), user_text=row["user_text"],
        response_text=row["response_text"], routed_to=row["routed_to"], created_at=row["created_at"],
    )


async def _recent_turns(limit: int = _RECENT_TURNS_LIMIT) -> list[ConversationTurn]:
    async with connection() as conn:
        rows = await conn.fetch(
            "SELECT * FROM conversation_turn WHERE session_id = $1 ORDER BY created_at DESC LIMIT $2;",
            _SESSION_ID, limit,
        )
    return [_row_to_turn(r) for r in reversed(rows)]  # chronological order for the prompt


def _format_recent_context(turns: list[ConversationTurn]) -> str:
    if not turns:
        return "(none yet — this is the first message this session)"
    return "\n".join(f"User: {t.user_text}\nJarvis: {t.response_text}" for t in turns)


# --- V1-M3: durable Memory, distinct from the session context above ---
# (Decision 6 — the whole point of keeping this a separate section in
# the prompt, with separate wording, is so the model never conflates
# "recalled from this sitting" with "recalled across sessions".)

# 2026-08-18: FIX for "do you know my name" bug — _format_known_memories
# must read the FULL Active set, not an importance-ranked top-N window.
# The user's real database has 10+ Active memories. `user's name` was set
# once, early, at default Medium importance, and never touched again.
# Every other memory created or updated since then is ALSO Medium
# importance, and more recent. Sorted by (importance_rank, updated_at)
# descending, `user's name` was getting ranked straight out of the top-5
# window and never reaching the model's context at all. No prompt wording
# can fix a bug where the actual fact was never shown to the model.
# Same failure mode already fixed once before (2026-08-16) for
# memory/extraction.py's known_memories param — this function just never
# got the equivalent fix. A generous sanity cap (50, not 5) guards
# against pathological growth; it is NOT a normal-case limit.
_MEMORY_RETRIEVAL_CAP = 50


async def _format_known_memories() -> str:
    memories = await list_memories(status=MemoryStatus.ACTIVE)
    if not memories:
        return "(nothing stored yet)"
    # Cap at _MEMORY_RETRIEVAL_CAP as a sanity guard against pathological
    # growth — not a normal-case limit. list_memories orders by created_at
    # ASC (oldest first), which gives the oldest facts (like "user's name")
    # a fair chance regardless of recency.
    if len(memories) > _MEMORY_RETRIEVAL_CAP:
        memories = memories[:_MEMORY_RETRIEVAL_CAP]
    return "\n".join(f"- {m.title}: {m.value}" for m in memories)


async def _apply_memory_policy(
    candidates: list[MemoryCandidate], source_id: str
) -> tuple[Optional[str], list[PendingMemoryConfirmation]]:
    """V1-M3 §6 pipeline: deterministic policy, per candidate ->
    immediate writes, pending confirmations, or nothing. Split out from
    extraction itself (2026-08-15b, latency fix — see
    ARCHITECTURE_ISSUES.md) so handle() can decide via the cheap
    _route() flag whether to even call extract_candidates() before this
    runs, instead of always paying for both calls. Returns
    (memory_grounding, pending) where memory_grounding is a combined,
    factual line _conversational_reply may surface to the user (the
    mechanism that lets a WRITE_DURABLE turn honestly say "I'll
    remember that" without tripping the capability-claim guard below —
    see that guard's updated docstring)."""
    if not candidates:
        return None, []

    written_notes: list[str] = []
    pending: list[PendingMemoryConfirmation] = []

    for candidate in candidates:
        decision = decide_memory_policy(candidate)

        if decision.outcome is PolicyOutcome.WRITE_DURABLE:
            await remember_from_candidate(candidate, source_id=source_id)
            written_notes.append(f"Just saved to memory: {candidate.title} — {candidate.value}.")
        elif decision.outcome is PolicyOutcome.ASK_CONFIRMATION:
            pending.append(PendingMemoryConfirmation(candidate, source_id, decision.reason))
        elif decision.outcome is PolicyOutcome.BLOCK_SENSITIVE:
            written_notes.append(f"Didn't save that — {decision.reason}")
        elif decision.outcome is PolicyOutcome.TAXONOMY_GAP:
            await record_taxonomy_gap(candidate)
        # NO_OP (session-scoped or non-explicit): nothing to do

    grounding = "\n".join(written_notes) if written_notes else None
    return grounding, pending


async def resolve_pending_memory(pending: PendingMemoryConfirmation, approved: bool) -> Optional[str]:
    """Called back by the CLI (or any future frontend) after the user
    answers the confirmation prompt. Returns a short status line for
    display, or None if declined."""
    if not approved:
        return None
    await remember_from_candidate(pending.candidate, source_id=pending.source_id)
    return f"Saved to memory: {pending.candidate.title} — {pending.candidate.value}."


async def _execute_state_change(candidate: StateChangeCandidate) -> str:
    """The single writer for V1-M2: every actual mutation goes through
    goals.api/mission.api's existing, already-tested functions — never
    new SQL, never a parallel write path. Called both from
    _apply_state_change_policy (EXECUTE outcome) and
    resolve_pending_state_change (after a y/N confirmation), so a
    confirmed Mission change and a directly-executed Goal creation
    report through the same shape."""
    if candidate.operation is StateChangeOperation.CREATE_GOAL:
        goal = await create_goal(type=candidate.type, title=candidate.title)
        return f"Created a new {goal.type.value}: \"{goal.title}\" ({goal.status.value})."

    if candidate.operation is StateChangeOperation.SET_GOAL_STATUS:
        goal = await set_goal_status(
            candidate.resolved_goal_id, candidate.new_status, reason="Natural-language state-change command"
        )
        return f"Updated \"{goal.title}\" to {goal.status.value}."

    mission = await set_mission(title=candidate.mission_title, statement=candidate.mission_statement)
    return f"Mission updated to \"{mission.title}\" (v{mission.version})."


async def _apply_state_change_policy(
    candidate: Optional[StateChangeCandidate],
) -> tuple[Optional[str], Optional[str], list["PendingStateChangeConfirmation"]]:
    """V1-M2 pipeline: deterministic policy, one candidate -> immediate
    execution, a pending y/N confirmation, or a relay note.

    Returns (executed_note, relay_note, pending):
    - executed_note: a completed-write fact ("Created a new Goal...")
      — safe to merge with memory's grounding and tell the reply model
      "you may state this happened", same contract as
      _apply_memory_policy's return.
    - relay_note: the command is NOT finished yet — either ASK_CLARIFY's
      question, REJECT_INVALID_TRANSITION's explanation, or (2026-08-27
      addition) ASK_CONFIRMATION's heads-up that a separate y/N prompt
      is coming. NEVER a completed action, so it must never be fed
      through the "this happened" framing (bug found in dogfooding
      2026-08-26: a create_goal with an unresolved type produced
      ASK_CLARIFY's reason text, which got merged into the "Memory
      action just taken this turn" block; the small model, told a
      pending question was a fact that already happened, produced an
      unrelated generic reply instead of asking the user what type of
      goal they meant).
    - ASK_CONFIRMATION also needs relay_note, not just `pending`: found
      in a second round of dogfooding 2026-08-27 — cli.py resolves the
      y/N prompt (built from `decision.reason`) and writes to the DB
      BEFORE it ever prints this function's separately-generated reply
      text (see cli.py's handle_line — pending loops run before
      response_text is printed). With no grounding at all, the reply
      model has zero idea a confirmation is even happening this turn,
      so it improvises — observed live, twice in the same session: "I
      can't delete goals right now" printed directly under
      "-> Updated 'Test' to Cancelled.", and "I can't create or modify
      missions right now" printed directly under "-> Mission updated to
      'Test mission' (v2)." — a direct, visible contradiction of a
      write that had already succeeded by the time that sentence
      appeared. Callers must surface this as its own labeled block
      instructing the model to relay it, not as a stated fact.
    Only one of executed_note/relay_note is ever non-None at a time.
    """
    if candidate is None:
        return None, None, []

    decision = decide_state_change_policy(candidate)

    if decision.outcome is StateChangePolicyOutcome.EXECUTE:
        note = await _execute_state_change(candidate)
        return note, None, []

    if decision.outcome is StateChangePolicyOutcome.ASK_CONFIRMATION:
        pending = [PendingStateChangeConfirmation(candidate, decision.reason)]
        relay = (
            f"still needs the user's yes/no confirmation, which will be "
            f"shown to them separately right after your reply, before "
            f"Jarvis proceeds — {decision.reason}"
        )
        return None, relay, pending

    # ASK_CLARIFY / REJECT_INVALID_TRANSITION: nothing written. No
    # stateful pending-clarification object: if the user retypes more
    # specifically, that next message just goes through this same
    # pipeline again. Keeping this stateless avoids a second, more
    # complex confirmation subsystem for a case a simple retry already
    # covers.
    return None, decision.reason, []


async def resolve_pending_state_change(pending: "PendingStateChangeConfirmation", approved: bool) -> Optional[str]:
    """Called back by the CLI (or any future frontend) after the user
    answers the confirmation prompt. Returns a short status line for
    display, or None if declined."""
    if not approved:
        return None
    return await _execute_state_change(pending.candidate)


@dataclass
class PendingStateChangeConfirmation:
    """V1-M2: returned instead of writing immediately when
    state_change.policy.decide() gives ASK_CONFIRMATION (Mission
    changes, and Goal transitions into Cancelled/Archived). Mirrors
    PendingMemoryConfirmation's shape/UX deliberately — same y/N loop
    in cli.py, same reasoning for staying off Permission/Action Engine
    (ARCHITECTURE_ISSUES.md 2026-08-15 entry) — but kept as its own
    dataclass rather than a shared one, since the payload (a
    StateChangeCandidate, not a MemoryCandidate) and the outcome enum
    it's paired with are both domain-specific."""
    candidate: StateChangeCandidate
    reason: str


@dataclass
class PendingMemoryConfirmation:
    """V1-M3: returned instead of writing immediately when
    candidate_policy.decide() gives ASK_CONFIRMATION (ambiguous scope).
    Mirrors the shape of orchestrator's `awaiting_confirmation` PCRs
    (cli.py already loops over those with a y/N prompt) — same UX
    pattern, deliberately NOT routed through PermissionCheckResult/
    Action Engine itself (V1-M3 non-goals: no capability expansion, no
    reworking Permission/Action — see ARCHITECTURE_ISSUES.md 2026-08-15
    entry)."""
    candidate: MemoryCandidate
    source_id: str
    reason: str


@dataclass
class ConversationOutcome:
    routed_to: Literal["conversation", "reasoning"]
    response_text: str
    pending_memories: list[PendingMemoryConfirmation] = field(default_factory=list)
    pending_state_changes: list[PendingStateChangeConfirmation] = field(default_factory=list)


def _strip_markdown_formatting(text: str) -> str:
    """Deterministic backstop for the 2026-08-23b prompt instruction —
    live dogfooding (2026-08-24) showed the model still emits
    '**bold**' despite being told this is a plain-text terminal, even
    after that fix. Same lesson this project has already learned twice
    (the extraction "explicit only" rule, the name-recall grounding
    rule): a prompt instruction is a nudge, not a guarantee, and for
    something this mechanical a deterministic pass is strictly more
    reliable than asking more forcefully a second time. Deliberately
    conservative — only strips symbols that are unambiguous outside
    real Markdown (headers, bold, horizontal rules). Does NOT touch
    single '*'/'_' (too easily legitimate — multiplication, ordinary
    emphasis, code-like tokens — to strip safely without a real
    Markdown parser)."""
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"^\s*(-{3,}|_{3,}|\*{3,})\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _matches_fast_path_greeting(text: str) -> bool:
    cleaned = re.sub(r"[^\w\s]", "", text.lower()).strip()
    words = cleaned.split()
    if not words or len(words) > _MAX_FAST_PATH_WORDS:
        return False
    return any(w in _GREETING_WORDS for w in words) and all(
        w in _GREETING_WORDS or w in _GREETING_FILLER for w in words
    )


@dataclass
class RouteDecision:
    """V1-M3 revision (2026-08-15b, latency): one routing call now
    answers both questions the old two-call design asked separately —
    see ARCHITECTURE_ISSUES.md's matching entry for why. Collapses the
    common case (ordinary message, nothing memory-worthy) from 2 LLM
    calls down to 1; extraction's own (more expensive, more careful)
    call only fires when memory_candidate_possible says it's worth it.

    2026-08-17 fix: "what do you know about me" was landing on
    needs_reasoning=True with no explicit example either way in
    _ROUTING_SYSTEM, sending it to Reflection's terse decision template
    instead of the conversational path — which already has an explicit
    instruction (_CONVERSATIONAL_SYSTEM) to answer exactly this kind of
    question directly from durable memory. See ARCHITECTURE_ISSUES.md's
    matching entry.

    V1-M2: state_change_possible added as a third field on this SAME
    call, same reasoning as memory_candidate_possible's addition —
    one more unconditional LLM call per message is exactly the latency
    regression the 2026-08-15b fix exists to prevent."""
    needs_reasoning: bool
    memory_candidate_possible: bool
    state_change_possible: bool


async def _route(user_text: str, recent_context: Optional[str] = None) -> RouteDecision:
    # max_tokens was 20 — fine for a model that answers with bare JSON, but
    # task_type="fast" routes to openrouter/free, which auto-selects
    # whatever free model is live and can land on a reasoning model that
    # emits a chain-of-thought preamble before the JSON. At 20 tokens the
    # reply gets truncated mid-reasoning, before any '{' exists for
    # complete_json's extractor to find, so it always fails closed. 300
    # gives room for a CoT preamble to finish; the fail-closed default
    # below is left as-is as a genuine backstop.
    #
    # 2026-08-27 dogfooding bug: recent_context added because a bare
    # follow-up answer to Jarvis's own clarifying question ("a Project",
    # answering "what kind of goal is this?") has no command language of
    # its own — routing on user_text alone always came back
    # state_change_possible=False for it, so state_change/extraction.py
    # never even ran regardless of what context IT was given. Passing
    # the same recent-conversation text costs no extra LLM call (this
    # call already happens every turn), just a bigger prompt.
    prompt = user_text
    if recent_context:
        prompt = f"Recent conversation this session:\n{recent_context}\n\nUser's current message: {user_text}"
    result = await complete_json(
        system=_ROUTING_SYSTEM, prompt=prompt, task_type="fast", max_tokens=300
    )
    if (
        result
        and isinstance(result.get("needs_reasoning"), bool)
        and isinstance(result.get("memory_candidate_possible"), bool)
        and isinstance(result.get("state_change_possible"), bool)
    ):
        return RouteDecision(
            result["needs_reasoning"], result["memory_candidate_possible"], result["state_change_possible"]
        )
    # §3: safer default on failure/garbage for needs_reasoning — an
    # under-grounded conversational answer risks accidentally asserting
    # something about the user's actual goals (BUG-M6-01's failure
    # class); routing into the rigorous path costs nothing but a
    # slightly more formal answer. Same logic for
    # memory_candidate_possible and state_change_possible: failing
    # closed to False would silently drop real content with no second
    # chance (unlike needs_reasoning, there's no downstream recovery),
    # so both fail OPEN to True instead — worst case is one avoidable
    # extraction call, not a lost memory or a silently-ignored command.
    log.info(
        "Conversation routing LLM call unavailable/invalid — defaulting "
        "to needs_reasoning=True, memory_candidate_possible=True, "
        "state_change_possible=True"
    )
    return RouteDecision(needs_reasoning=True, memory_candidate_possible=True, state_change_possible=True)


async def _conversational_reply(
    user_text: str, mission: Mission, memory_grounding: Optional[str] = None, relay_note: Optional[str] = None,
    recent: Optional[list[ConversationTurn]] = None,
) -> str:
    # 2026-08-27: recent is now an optional pass-through from handle()
    # (which needs its own copy for _route()/extract_state_change) —
    # still fetched here if a caller doesn't have one, so this stays
    # correct standalone.
    if recent is None:
        recent = await _recent_turns()
    known_memories = await _format_known_memories()
    prompt = (
        f"Mission: {mission.title}\n"
        f"Capabilities: {_capability_grounding()}\n"
        f"Interface: {_REAL_INTERFACE}\n"
        f"What you know about the user (durable memory, persists across "
        f"sessions — distinct from the recent-conversation section below):\n{known_memories}\n"
        f"Recent conversation this session:\n{_format_recent_context(recent)}\n"
    )
    if memory_grounding:
        # Only path allowed to make a first-person persistence claim true
        # this turn — see _claims_unbacked_action's updated docstring.
        # Covers both Memory saves and V1-M2 Goal/Mission writes — both
        # are completed-write facts by the time this is non-None.
        prompt += (
            f"Action just taken this turn, directly in response to the "
            f"user's request — say so as something you just did for "
            f"them (\"done\", \"I've set...\", \"I've created...\"), never as "
            f"a pre-existing fact (\"it's already...\") — this is a change "
            f"that just happened, not a status you're merely reporting: "
            f"{memory_grounding}\n"
        )
    if relay_note:
        # V1-M2: NOT a completed action — the command is still in
        # progress. Kept in its own block, deliberately never merged
        # with memory_grounding above (see _apply_state_change_policy's
        # docstring for the 2026-08-26 dogfooding bug this fixes).
        #
        # 2026-08-27, round 1: the first version of this block said
        # "Jarvis can't complete yet", which combined with
        # _CONVERSATIONAL_SYSTEM's default "if you can't do something,
        # say so plainly" instruction to make the model prepend a false
        # "I can't do that" disclaimer even while correctly asking the
        # clarifying question right after it.
        #
        # 2026-08-27, round 2: relay_note now also covers ASK_CONFIRMATION
        # (a separate y/N prompt is about to show), not just ASK_CLARIFY
        # (a question) — same underlying problem, worse symptom: with no
        # grounding at all for the confirmation case, the model said "I
        # can't delete goals" / "I can't create or modify missions"
        # printed directly beneath cli.py's own "-> Updated ... to
        # Cancelled." / "-> Mission updated to ..." lines from the SAME
        # turn — a live, visible contradiction of a write that had
        # already happened by the time that sentence was shown. The
        # wording below must hold for both cases: never claim inability,
        # and never claim the thing is already done, since for
        # ASK_CONFIRMATION it explicitly is NOT done yet.
        prompt += (
            f"Jarvis is actively handling a Goal/Mission command from the "
            f"user right now and it isn't finished yet — this is a "
            f"normal in-progress step, not something Jarvis lacks the "
            f"ability to do, so don't tell the user you can't do this. "
            f"It is also NOT done yet, so don't say or imply that it is. "
            f"Just address this naturally, in your own words: {relay_note}\n"
        )
    prompt += f"User: {user_text}"

    try:
        reply = (await complete(
            system=_CONVERSATIONAL_SYSTEM, prompt=prompt, task_type="conversation", max_tokens=200
        )).strip()
    except LLMUnavailableError as e:
        log.info(f"Conversational reply unavailable — using fallback: {e}")
        return memory_grounding or relay_note or _FALLBACK_REPLY

    if _leaks_prompt_structure(reply):
        log.warning(
            "Conversational reply rejected — echoed the prompt's own field labels "
            "(prompt-leakage guard); using generic fallback"
        )
        return _MALFORMED_REPLY_FALLBACK

    if _leaks_reasoning_preamble(reply):
        log.warning(
            "Conversational reply rejected — leaked a raw chain-of-thought "
            "preamble instead of a final answer (reasoning-leak guard); "
            "using generic fallback"
        )
        return _MALFORMED_REPLY_FALLBACK
    if _claims_unbacked_action(reply) and not memory_grounding:
        log.warning(
            "Conversational reply rejected — claimed an action this path cannot "
            "perform (capability-claim guard); using honest fallback"
        )
        return _CANNOT_PERSIST_REPLY
    if _mentions_fabricated_ui(reply):
        log.warning(
            "Conversational reply rejected — referenced a UI element that doesn't "
            "exist (fabricated-UI guard); using honest fallback"
        )
        return _NO_GUI_REPLY
    return _strip_markdown_formatting(reply)


_GROUNDED_SYSTEM = (
    "You are Jarvis, answering a question that needs real grounding in "
    "the user's actual goals, mission, and memory — not just casual "
    "conversation. You'll be given structured evidence: the Mission, "
    "every Goal with its real status and relationships, and relevant "
    "Memories. Answer the user's actual question naturally and "
    "specifically — lay out a real plan if asked, explain how something "
    "affects their mission or goals if asked, reflect on progress if "
    "asked, or anything else in this vein — like a genuinely capable "
    "assistant would, not a fixed report format. You will also be given "
    "'Recent conversation this session' the same way an ordinary "
    "conversational reply is, so follow-ups like 'change that part' or "
    "'no, the other one' work naturally — this is a real back-and-forth, "
    "not a one-shot report.\n\n"
    "The rules below are non-negotiable, no matter how natural or "
    "conversational the answer is:\n"
    "- Ground every claim ONLY in the evidence given. Never invent a "
    "goal, memory, or fact not present in it.\n"
    "- Never state or imply two goals are related, connected, dependent "
    "on each other, or that one supports another, unless that exact "
    "link appears in that goal's own 'relationships' field. An empty "
    "list means independent — treat it as such.\n"
    "- Never call a Goal 'the mission,' and never state a mission title "
    "other than the exact one in the 'mission' object.\n"
    "- Never call a goal 'active' (or any other status) other than "
    "exactly what its real 'status' field says.\n"
    "- This system has no calendar/scheduling capability yet — a 'plan' "
    "you lay out is a suggested breakdown to discuss and refine "
    "together, not something that gets scheduled or executed on its "
    "own. Don't claim to have created, scheduled, or saved a plan "
    "anywhere; you're proposing one in conversation.\n"
    "- This is a PLAIN TEXT TERMINAL, not a rendered chat UI — Markdown "
    "does not render here. NEVER use '#'/'##' headers, '**bold**', "
    "horizontal rules ('---'), or any other Markdown syntax; it will "
    "show up as ugly literal symbols, not formatting. For a multi-part "
    "answer like a schedule or a plan, use plain numbered steps ('1.', "
    "'2.', ...) or a simple dash per item, with plain sentences — "
    "nothing else."
)


def _render_evidence_plainly(context_items, mission) -> str:
    """Deterministic, guaranteed-accurate fallback for when the free-form
    grounded reply is unavailable or trips an honesty guard below — the
    same safety guarantee the old rigid Decision template gave (see
    ARCHITECTURE_ISSUES.md), without falling back to a fixed report
    format for the normal case. Built directly from the evidence
    already gathered, not generated text, so it can't itself be wrong."""
    goal_items = [i for i in context_items if i.source_type == ContextSourceType.GOAL]
    memory_items = [i for i in context_items if i.source_type == ContextSourceType.MEMORY]
    lines = [
        "I want to be careful not to state something about your goals or "
        "mission that I'm not sure is accurate — here's what I actually "
        "have on record:",
        f"Mission: {mission.title}",
    ]
    if goal_items:
        lines.append("Goals:")
        lines += [f"  - {i.payload.get('title')} ({i.payload.get('status')})" for i in goal_items]
    else:
        lines.append("Goals: none yet.")
    lines.append(f"Memories on record: {len(memory_items)}.")
    return "\n".join(lines)


async def _persist_grounded_decision(user_text: str, response_text: str, context_items, mission) -> None:
    """Traceability/explainability for the grounded-conversation path —
    PROJECT.md: 'Jarvis should always be able to explain: why it made a
    decision, what information it used.' Always requires_plan=False:
    ordinary conversation, however grounded, never auto-fires a Plan or
    Action anymore — see this module's docstring. intent='Conversation'
    is a new, free-text value (the `decision.intent` column has no
    CHECK constraint) distinguishing these from reason()'s still-intact
    Planning/Reflection rows."""
    goal_items = [i for i in context_items if i.source_type == ContextSourceType.GOAL]
    decision = Decision(
        objective=user_text,
        summary=response_text,
        reasoning=f"Grounded conversational synthesis over {len(goal_items)} goal(s) "
                  f"and {len(context_items) - len(goal_items)} memory item(s), mission '{mission.title}'.",
        confidence=1.0,  # the answer is guard-checked against real evidence, not a probabilistic guess
        selected_option=response_text,
        expected_outcome="A grounded, conversational answer — no state change.",
        mission_alignment=1.0,  # retrieval was already scoped to the active Mission
        goal_ids=[i.source_id for i in goal_items],
        context_item_ids=[i.id for i in context_items],
        requires_plan=False,
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
            "Conversation", decision.created_at, decision.version,
        )


async def _grounded_reply(
    user_text: str, mission, memory_grounding: Optional[str] = None, relay_note: Optional[str] = None,
    recent: Optional[list[ConversationTurn]] = None,
) -> str:
    """Replaces orchestrator.api.run_request() on the conversational
    path (2026-08-2X — see ARCHITECTURE_ISSUES.md and this module's
    docstring). Reuses reasoning/api.py's exact evidence-gathering and
    honesty guards; the only thing that changes is the output is a real,
    free-form answer instead of a rigid two-sentence template, and
    nothing here ever triggers a Plan or Action."""
    context_items, evidence = await gather_grounded_evidence(mission)
    if recent is None:
        recent = await _recent_turns()
    prompt = (
        f"Evidence: {evidence}\n"
        f"Recent conversation this session:\n{_format_recent_context(recent)}\n"
    )
    if memory_grounding:
        prompt += (
            f"Action just taken this turn, directly in response to the "
            f"user's request — say so as something you just did for "
            f"them (\"done\", \"I've set...\", \"I've created...\"), never as "
            f"a pre-existing fact (\"it's already...\") — this is a change "
            f"that just happened, not a status you're merely reporting: "
            f"{memory_grounding}\n"
        )
    if relay_note:
        # See _conversational_reply's matching block — same "in
        # progress, neither incapable nor already done" framing applies
        # here, covering both ASK_CLARIFY (a question) and
        # ASK_CONFIRMATION (a pending y/N) — see
        # _apply_state_change_policy's docstring for the 2026-08-27
        # round-2 dogfooding bug this fixes.
        prompt += (
            f"Jarvis is actively handling a Goal/Mission command from the "
            f"user right now and it isn't finished yet — don't tell the "
            f"user you can't do this, and don't say or imply it's already "
            f"done either. Just address this naturally: {relay_note}\n"
        )
    prompt += f"User: {user_text}"

    try:
        reply = (await complete(
            system=_GROUNDED_SYSTEM, prompt=prompt, task_type="complex", max_tokens=1200
        )).strip()
    except LLMUnavailableError as e:
        log.info(f"Grounded reply unavailable — using plain evidence fallback: {e}")
        reply = _render_evidence_plainly(context_items, mission)
        await _persist_grounded_decision(user_text, reply, context_items, mission)
        return reply

    # Per-guard checks with distinct log messages, not one combined OR —
    # 2026-08-23c: the collapsed version made a real false-positive
    # (see _asserts_wrong_status's revision note) much harder to
    # diagnose than it needed to be, since the log couldn't say which
    # guard actually fired. Matches reasoning/api.py's own
    # _enhance_with_llm, which never had this problem.
    if _looks_garbled(reply):
        log.warning("Grounded reply rejected — output looks garbled/corrupted; using plain evidence fallback")
        reply = _render_evidence_plainly(context_items, mission)
    elif _asserts_unsupported_relationship(reply, context_items):
        log.warning(
            "Grounded reply rejected — asserted a goal relationship not present in "
            "the data (BUG-M6-01 guard); using plain evidence fallback"
        )
        reply = _render_evidence_plainly(context_items, mission)
    elif _asserts_wrong_mission(reply, mission, context_items):
        log.warning("Grounded reply rejected — conflated a Goal with the Mission; using plain evidence fallback")
        reply = _render_evidence_plainly(context_items, mission)
    elif _asserts_wrong_status(reply, context_items):
        log.warning(
            "Grounded reply rejected — called a goal 'active' when its real status "
            "says otherwise; using plain evidence fallback"
        )
        reply = _render_evidence_plainly(context_items, mission)

    reply = _strip_markdown_formatting(reply)
    await _persist_grounded_decision(user_text, reply, context_items, mission)
    return reply


async def _archive(
    user_text: str, response_text: str, routed_to: Literal["conversation", "reasoning"],
    turn_id: Optional[str] = None,
) -> None:
    # V1-M3: turn_id is settable so a memory write this same turn (see
    # handle()) can use the archived ConversationTurn's own id as its
    # source_id — a real, already-persisted row, not a synthetic string.
    kwargs = dict(session_id=_SESSION_ID, user_text=user_text, response_text=response_text, routed_to=routed_to)
    if turn_id is not None:
        kwargs["id"] = turn_id
    turn = ConversationTurn(**kwargs)
    async with connection() as conn:
        await conn.execute(
            """
            INSERT INTO conversation_turn (id, session_id, user_text, response_text, routed_to, created_at)
            VALUES ($1, $2, $3, $4, $5, $6);
            """,
            turn.id, turn.session_id, turn.user_text, turn.response_text, turn.routed_to, turn.created_at,
        )


async def handle(user_text: str) -> ConversationOutcome:
    mission = await get_active_mission()
    if mission is None:
        await _archive(user_text, _ONBOARDING_MESSAGE, "conversation")
        return ConversationOutcome(routed_to="conversation", response_text=_ONBOARDING_MESSAGE)

    if _matches_fast_path_greeting(user_text):
        # Never memory-worthy by construction (word list is all
        # greetings/fillers, see _GREETING_WORDS) — skip the extraction
        # call entirely rather than pay for an LLM round-trip on "hi".
        response = await _conversational_reply(user_text, mission)
        await _archive(user_text, response, "conversation")
        return ConversationOutcome(routed_to="conversation", response_text=response)

    turn_id = str(uuid4())
    # 2026-08-27: fetched once here and reused for routing, state-change
    # extraction, and whichever reply function runs — one snapshot for
    # the whole turn instead of each stage re-querying independently.
    # Needed so _route() can see it too (see _route's docstring) — a
    # bare follow-up like "a Project" has no command language on its
    # own; only the preceding exchange makes it resolvable at all.
    recent = await _recent_turns()
    recent_context = _format_recent_context(recent)
    route = await _route(user_text, recent_context=recent_context)

    # 2026-08-15b latency fix: extraction's own (more expensive) LLM
    # call only fires when the single combined routing call above
    # already thinks it's plausible — see ARCHITECTURE_ISSUES.md. An
    # ordinary non-memory message now costs exactly the calls it did
    # before M3 existed (route + reply, or route + reasoning).
    # 2026-08-16: known-memory titles are fetched (a DB read, not an LLM
    # call — doesn't reopen the latency fix above) so extraction can
    # reuse an exact existing title on a restatement/correction instead
    # of inventing a new one — see memory/extraction.py's module
    # docstring for the live contradiction this fixes. Uses the full
    # Active set, not _memory_retriever's importance-ranked top-5 (that
    # ranking is for what's worth mentioning in a reply, not for giving
    # title-matching a fair shot at the specific thing being corrected —
    # _build_prompt on the extraction side still caps what's shown).
    if route.memory_candidate_possible:
        known = await list_memories(status=MemoryStatus.ACTIVE)
        candidates = await extract_candidates(
            user_text, known_memories=[(m.title, m.value, m.type.value) for m in known]
        )
    else:
        candidates = []
    memory_grounding, pending_memories = await _apply_memory_policy(candidates, source_id=turn_id)

    # V1-M2, same latency discipline as the memory branch just above:
    # the goal list (a DB read, not an LLM call) is only fetched, and
    # state_change/extraction.py's own LLM call only fires, when the
    # single combined routing call already thinks a state-change
    # command is plausible.
    if route.state_change_possible:
        known_goals = await list_goals(mission_id=mission.identity_id)
        state_candidate = await extract_state_change(
            user_text,
            known_goals=[(g.title, g.id, g.status.value) for g in known_goals],
            active_mission_title=mission.title,
            recent_context=recent_context,
        )
    else:
        state_candidate = None
    state_change_executed, state_change_relay, pending_state_changes = await _apply_state_change_policy(
        state_candidate
    )

    # Only completed-write facts get merged and framed as "this
    # happened" — state_change_relay (a clarifying question or a
    # rejection explanation) is NOT a fact and must stay in its own
    # labeled block, or the reply model treats a pending question as
    # something already done. See _apply_state_change_policy's
    # docstring for the dogfooding bug (2026-08-26) this fixes.
    combined_grounding = "\n".join(g for g in (memory_grounding, state_change_executed) if g) or None

    if route.needs_reasoning:
        response_text = await _grounded_reply(
            user_text, mission, memory_grounding=combined_grounding, relay_note=state_change_relay, recent=recent
        )
        await _archive(user_text, response_text, "reasoning", turn_id=turn_id)
        return ConversationOutcome(
            routed_to="reasoning", response_text=response_text,
            pending_memories=pending_memories, pending_state_changes=pending_state_changes,
        )

    response = await _conversational_reply(
        user_text, mission, memory_grounding=combined_grounding, relay_note=state_change_relay, recent=recent
    )
    await _archive(user_text, response, "conversation", turn_id=turn_id)
    return ConversationOutcome(
        routed_to="conversation", response_text=response,
        pending_memories=pending_memories, pending_state_changes=pending_state_changes,
    )
