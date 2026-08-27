"""
Reasoning API (§7: public interface for the merged Context+Decision
module; §9 M3). Per §5.5, this is architecturally still two contracts
(ContextItem, Decision) in one package for V0.

`reason(intent, objective)` is the single public entry point, running the
full pipeline: Retrieve (policies.py) -> Score -> Rank -> Budget Cutoff
(scoring.py) -> Decision generation -> persist.

ARCHITECTURE ISSUE (see ARCHITECTURE_ISSUES.md, M3 entry): Decision
generation here is template-based, not a live LLM call. There's no
`infra/llm_router.py` built yet (that's arguably M6's job, per the repo
structure putting LLM selection under Orchestrator/OR-04), and no
credential wiring exists in this environment for Jarvis to call out to a
model on its own behalf. This is also simply the correct "simplest thing
that satisfies the contract" per architect guidance (2026-08-02) — a
Decision needs `reasoning`, `confidence`, and evidence, none of which
strictly requires an LLM for V0's two narrow intents.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from config.loader import load_config
from contracts.decision import Decision
from contracts.enums import ContextSourceType, GoalStatus
from infra.llm_router import complete_json
from infra.logging import get_logger
from infra.storage import connection
from mission.api import get_active_mission
from reasoning.policies import RETRIEVAL_POLICIES, reflection_policy
from reasoning.scoring import apply_budget, score_and_rank

log = get_logger("reasoning.api")


class UnknownIntentError(Exception):
    pass


class NoActiveMissionError(Exception):
    pass


def _row_to_decision(row) -> Decision:
    return Decision(
        id=str(row["id"]),
        objective=row["objective"],
        summary=row["summary"],
        reasoning=row["reasoning"],
        confidence=row["confidence"],
        alternatives=json.loads(row["alternatives"]),
        selected_option=row["selected_option"],
        expected_outcome=row["expected_outcome"],
        risks=json.loads(row["risks"]),
        mission_alignment=row["mission_alignment"],
        goal_ids=json.loads(row["goal_ids"]),
        context_item_ids=json.loads(row["context_item_ids"]),
        requires_plan=row["requires_plan"],
        estimated_confirmation_needed=row["estimated_confirmation_needed"],
        created_at=row["created_at"],
        version=row["version"],
    )


async def get_decision(decision_id: str) -> Optional[Decision]:
    async with connection() as conn:
        row = await conn.fetchrow("SELECT * FROM decision WHERE id = $1;", decision_id)
        return _row_to_decision(row) if row else None


async def list_decisions(intent: Optional[str] = None) -> list[Decision]:
    if intent is not None:
        query = "SELECT * FROM decision WHERE intent = $1 ORDER BY created_at DESC;"
        params = [intent]
    else:
        query = "SELECT * FROM decision ORDER BY created_at DESC;"
        params = []
    async with connection() as conn:
        rows = await conn.fetch(query, *params)
        return [_row_to_decision(r) for r in rows]


async def list_decisions_with_intent(limit: int = 10) -> list[tuple[str, Decision]]:
    """CLI/debugging helper (P5, Jarvis_V0_Testing_Review — DE-08
    Explainability: '/status shows a count, nothing to inspect them').
    `Decision` has no `intent` field by design (it's a storage column,
    not part of the immutable §6.5 contract — see `reason()`'s INSERT
    below), so this pairs each row's intent with its Decision for
    display without adding a field to the contract itself."""
    async with connection() as conn:
        rows = await conn.fetch(
            "SELECT * FROM decision ORDER BY created_at DESC LIMIT $1;", limit
        )
        return [(r["intent"], _row_to_decision(r)) for r in rows]


def _template_planning_decision(objective: str, context_items, mission_id: str) -> Decision:
    goal_items = [i for i in context_items if i.source_type == ContextSourceType.GOAL]
    goal_items_ranked = sorted(goal_items, key=lambda i: i.relevance_score, reverse=True)

    if not goal_items_ranked:
        return Decision(
            objective=objective,
            summary="No active or draft goals found to plan around.",
            reasoning="The Planning retrieval policy returned no Goal candidates for the "
                      "active Mission — there's nothing to prioritize yet.",
            confidence=1.0,  # certain about the absence, not a guess
            alternatives=[],
            selected_option="Create a goal before planning.",
            expected_outcome="No change until a Goal exists.",
            risks=[],
            mission_alignment=1.0,
            goal_ids=[],
            context_item_ids=[i.id for i in context_items],
            requires_plan=False,
            estimated_confirmation_needed=False,
        )

    top = goal_items_ranked[0]
    alternatives = [i.payload["title"] for i in goal_items_ranked[1:3]]
    top_reasons = [i.reasoning for i in context_items[:5]]

    return Decision(
        objective=objective,
        summary=f"Prioritize '{top.payload['title']}' — it's the highest-relevance "
                f"{top.payload['status'].lower()} goal right now.",
        reasoning=" ".join(top_reasons),
        confidence=round(top.relevance_score, 2),
        alternatives=alternatives,
        selected_option=top.payload["title"],
        expected_outcome=f"Progress on '{top.payload['title']}' becomes the focus of the next Plan.",
        risks=[] if len(goal_items_ranked) > 1 else ["Only one active goal — limited alternatives considered."],
        mission_alignment=1.0,  # retrieval was already scoped to the active Mission
        goal_ids=[i.source_id for i in goal_items_ranked],
        context_item_ids=[i.id for i in context_items],
        requires_plan=True,
        estimated_confirmation_needed=False,
    )


def _template_reflection_decision(objective: str, context_items, mission_id: str) -> Decision:
    goal_items = [i for i in context_items if i.source_type == ContextSourceType.GOAL]
    memory_items = [i for i in context_items if i.source_type == ContextSourceType.MEMORY]
    completed = [i for i in goal_items if i.payload.get("status") == GoalStatus.COMPLETED.value]
    active = [i for i in goal_items if i.payload.get("status") == GoalStatus.ACTIVE.value]

    summary = (
        f"{len(completed)} goal(s) completed, {len(active)} still active, "
        f"{len(memory_items)} relevant memories on record."
    )
    avg_confidence = (
        sum(i.confidence for i in context_items) / len(context_items) if context_items else 1.0
    )

    return Decision(
        objective=objective,
        summary=summary,
        reasoning=" ".join(i.reasoning for i in context_items[:5]) or "No recent activity found.",
        confidence=round(avg_confidence, 2),
        alternatives=[],
        selected_option=summary,
        expected_outcome="A clearer picture of recent progress, no state change.",
        risks=[],
        mission_alignment=1.0,
        goal_ids=[i.source_id for i in goal_items],
        context_item_ids=[i.id for i in context_items],
        requires_plan=False,
        estimated_confirmation_needed=False,
    )


_DECISION_BUILDERS = {
    "Planning": _template_planning_decision,
    "Reflection": _template_reflection_decision,
}

# Real LLM replacement (architect decision, 2026-08-04): the template
# builders above stay exactly as-is and become the fallback path — every
# ID/traceability field (goal_ids, context_item_ids, selected_option,
# confidence, mission_alignment) stays computed deterministically in
# Python, since an LLM should never be trusted to invent real IDs. Only
# `summary`/`reasoning` (the prose) get a chance to be replaced by the
# LLM, grounded strictly in the evidence already gathered.
#
# BUG-M6-01 fix (2026-08-05): plain-text evidence ("Goal A is Draft. Goal
# B is Draft.") gave the LLM room to narrate a relationship between two
# goals that share nothing but a sentence boundary. Two changes:
#   1. Evidence is now structured JSON with an explicit `relationships`
#      field per goal (sourced only from Goal.parent_goal_id/dependencies
#      — contracts/goal.py). An empty list is a fact, not an absence of
#      information, and the prompt says so.
#   2. The LLM's output is validated after the fact: if it asserts a
#      relationship between two goals that isn't in that structured data,
#      the enhancement is rejected and the template (which never invents
#      relationships) is used instead. Prompting reduces the chance;
#      validation is what actually guarantees it (evidence-first,
#      PROJECT.md Explainability).
_LLM_SYSTEM = (
    "You are Jarvis's reasoning engine. You receive structured evidence as "
    "JSON: the user's Mission (an object with a 'title'), a list of goals "
    "(each with an explicit 'relationships' field), and a list of "
    "memories. Write a one-line summary and a short reasoning explanation "
    "grounded ONLY in this evidence — never invent facts not present in "
    "it. Critically: never state or imply that two goals are related, "
    "connected, dependent on each other, or that one supports the other, "
    "unless that exact link appears in a goal's own 'relationships' "
    "field. An empty 'relationships' list means that goal is independent "
    "— treat it as such. Also critically: never call a goal 'the "
    "mission,' and never state a mission title other than the exact one "
    "given in the 'mission' object — a Goal and the Mission are never the "
    "same thing, even if the user's question uses the word 'mission.' "
    "Respond ONLY with JSON: "
    '{"summary": "...", "reasoning": "..."}'
)
_LLM_TASK_TYPE = {"Planning": "complex", "Reflection": "conversation"}

_RELATIONAL_PHRASES = [
    "related to", "relates to", "relationship with", "connected to",
    "connection between", "depends on", "dependency on", "dependent on",
    "linked to", "tied to", "supports", "in support of", "contributes to",
    "because it advances", "affects", "blocks", "is part of",
    "requires completion of",
]


def _build_structured_evidence(context_items, mission) -> str:
    """Serialize goal facts with their real relationship data so the LLM
    has no textual gap to invent connections in. Only parent_goal_id /
    dependencies (contracts/goal.py) count as a relationship — nothing
    inferred, nothing implicit.

    Fix (2026-08-10, found in dogfooding): the Mission's actual title was
    never included here — only Goal/Memory evidence was. When a user's
    question happened to contain the word "mission" (e.g. "what missions
    do i have?"), the LLM had no real Mission data to draw from and
    confidently relabeled the top-ranked Goal as "the mission" instead —
    wrong, but stated with full confidence, which is worse than the old
    honest "insufficient information" failure it replaced. Same fix
    shape as BUG-M6-01: give the real object explicitly instead of
    leaving a gap for the model to fill in."""
    goal_items = [i for i in context_items if i.source_type == ContextSourceType.GOAL]
    memory_items = [i for i in context_items if i.source_type == ContextSourceType.MEMORY]
    ids_in_context = {i.source_id for i in goal_items}

    goals_block = []
    for i in goal_items:
        parent = i.payload.get("parent_goal_id")
        deps = [d for d in (i.payload.get("dependencies") or []) if d in ids_in_context]
        rels = []
        if parent and parent in ids_in_context:
            rels.append(f"parent_of:{parent}")
        rels += [f"depends_on:{d}" for d in deps]
        goals_block.append({
            "id": i.source_id,
            "title": i.payload.get("title"),
            "status": i.payload.get("status"),
            "relationships": rels,
        })

    memories_block = [{"summary": i.reasoning} for i in memory_items]

    return json.dumps({
        "mission": {"title": mission.title},
        "goals": goals_block,
        "memories": memories_block,
        "relationship_note": (
            "A goal's 'relationships' list is the complete, authoritative set "
            "of its known relationships. An empty list means no known "
            "relationship exists between that goal and any other goal here."
        ),
        "mission_note": (
            "The 'mission' object above is the user's one and only Mission. "
            "None of the entries under 'goals' are the Mission, even if the "
            "user's question uses the word 'mission' — a Goal and the "
            "Mission are never the same thing."
        ),
    })


def _known_related_title_pairs(context_items) -> set[tuple[str, str]]:
    goal_items = [i for i in context_items if i.source_type == ContextSourceType.GOAL]
    title_by_id = {i.source_id: i.payload.get("title", "") for i in goal_items}
    pairs: set[tuple[str, str]] = set()
    for i in goal_items:
        related_ids = list(i.payload.get("dependencies") or [])
        parent = i.payload.get("parent_goal_id")
        if parent:
            related_ids.append(parent)
        for other_id in related_ids:
            other_title = title_by_id.get(other_id)
            if other_title:
                pairs.add(tuple(sorted((i.payload.get("title", ""), other_title))))
    return pairs


def _asserts_unsupported_relationship(text: str, context_items) -> bool:
    """Deterministic guard for BUG-M6-01: reject LLM prose that names two
    goals together alongside relational language, unless that pair has an
    explicit relationship in the data. Heuristic, not exhaustive (§3
    Principle #11 — a hypothesis, log and refine if real usage finds
    gaps) — but a false rejection just falls back to the template, which
    is always safe, so the cost of a miss is low.

    Revision 2026-08-23c: same whole-document co-occurrence flaw fixed in
    _asserts_wrong_status just below — a relational phrase ("affects",
    "supports", etc.) anywhere in a longer free-form answer, plus two
    goal titles mentioned anywhere else in it for unrelated reasons,
    used to count as a violation even with no actual claim linking them.
    The old rigid enhancement's short output rarely surfaced this; the
    2026-08-23 grounded-conversation redesign's genuinely multi-sentence
    answers can easily mention several goals and use an ordinary
    relational word in different, unconnected sentences. Now requires
    both the phrase and the pair of titles in the SAME sentence."""
    lowered = text.lower()
    if not any(phrase in lowered for phrase in _RELATIONAL_PHRASES):
        return False

    goal_items = [i for i in context_items if i.source_type == ContextSourceType.GOAL]
    titles = [i.payload.get("title", "") for i in goal_items if i.payload.get("title")]
    known_pairs = _known_related_title_pairs(context_items)

    for sentence in re.split(r"(?<=[.!?\n])\s+", lowered):
        if not any(phrase in sentence for phrase in _RELATIONAL_PHRASES):
            continue
        mentioned = [t for t in titles if t.lower() in sentence]
        if len(mentioned) < 2:
            continue
        for a in range(len(mentioned)):
            for b in range(a + 1, len(mentioned)):
                pair = tuple(sorted((mentioned[a], mentioned[b])))
                if pair not in known_pairs:
                    return True
    return False


_MAX_TOKEN_LEN = 24


def _looks_garbled(text: str) -> bool:
    """Sanity guard found necessary in dogfooding, 2026-08-06: an
    openrouter fallback response once produced 'Hajdus(firstcaps(m)arker
    vetically=Active)' embedded in otherwise-normal prose, and it went
    straight into a persisted Decision because nothing checked basic
    output coherence. Not a grammar checker — just the two cheapest,
    highest-signal corruption markers: unbalanced parentheses, and a
    single token too long/dense for natural language. A false positive
    just falls back to the always-safe template, so this stays cheap on
    purpose rather than trying to be a real coherence classifier."""
    if text.count("(") != text.count(")"):
        return True
    return any(len(token) > _MAX_TOKEN_LEN for token in text.split())


def _asserts_wrong_mission(text: str, mission, context_items) -> bool:
    """Deterministic guard, found in dogfooding 2026-08-10: the LLM once
    confidently called a Goal 'the mission' ("Your active mission is to
    Build jarvis v1.") when the real Mission title was never in its
    evidence at all — a wrong answer stated with full confidence, worse
    than the old honest 'insufficient information' fallback it replaced.
    Now that the real title IS in the evidence (_build_structured_
    evidence), this catches the case where the model still doesn't use
    it: text talks about 'mission' but never actually contains the real
    Mission title, while a Goal title is present instead — the exact
    conflation signature. Same cost trade-off as the other guards here:
    a false rejection just falls back to the always-safe template."""
    lowered = text.lower()
    if "mission" not in lowered:
        return False
    if mission.title.lower() in lowered:
        return False  # correctly used the real title somewhere

    goal_items = [i for i in context_items if i.source_type == ContextSourceType.GOAL]
    titles = [i.payload.get("title", "") for i in goal_items if i.payload.get("title")]
    return any(t.lower() in lowered for t in titles if t)


def _asserts_wrong_status(text: str, context_items) -> bool:
    """Deterministic guard, found in dogfooding 2026-08-14: the LLM
    called two Draft goals "active" ("You have two active goals: Build
    jarvis and complete an ironman race.") and then justified it by
    misreading its own evidence back at the user ("status 'Draft',
    indicating they are active"). Draft and Active are distinct,
    mutually exclusive values (contracts/enums.py GoalStatus) — Draft
    never implies Active, no more than Completed implies Active. This
    is the same failure shape as the relationship/mission guards above
    (an LLM confidently overriding a fact the structured evidence
    already gave it) — narrowly scoped to the 'active' claim
    specifically, since that's the one that would actually mislead the
    user about what's currently actionable, and a broader multi-status
    matcher would false-positive on ordinary negations like 'no goals
    completed yet.' Same cost trade-off: a false rejection just falls
    back to the always-safe template, which already renders each goal's
    real status correctly.

    Revision 2026-08-23c: originally checked whole-text co-occurrence
    ("active" anywhere + a non-active goal's title anywhere) rather than
    whether the text actually calls THAT goal active. Harmless against
    the old rigid enhancement's short two-sentence output, but the
    2026-08-23 grounded-conversation redesign made this guard's real
    caller a genuinely free-form, multi-paragraph answer — and with
    exactly one Active goal alongside any Draft ones, virtually any
    accurate answer mentioning the active goal by name AND listing the
    other goals for completeness ("Build Jarvis is your active project;
    your other goals like Run an ironman run are still in Draft") now
    satisfied the old whole-text check and got rejected every single
    time, even though nothing false was said. Found via live dogfooding
    — three consecutive real replies rejected, 100% of them accurate.
    Now checks sentence-level proximity: "active" and a specific
    non-active goal's title must appear in the SAME sentence to count,
    matching the original bug's own construction ("You have two active
    goals: Build jarvis and complete an ironman race." — one sentence)
    without flagging accurate separate mentions."""
    lowered = text.lower()
    if not re.search(r"\bactive\b", lowered):
        return False

    goal_items = [i for i in context_items if i.source_type == ContextSourceType.GOAL]
    non_active = [
        (i.payload.get("title") or "").lower()
        for i in goal_items
        if i.payload.get("title") and (i.payload.get("status") or "").lower() != "active"
    ]
    if not non_active:
        return False

    for sentence in re.split(r"(?<=[.!?\n])\s+", lowered):
        if not re.search(r"\bactive\b", sentence):
            continue
        if any(title in sentence for title in non_active):
            return True
    return False


async def _enhance_with_llm(decision: Decision, intent: str, context_items, mission) -> Decision:
    evidence = _build_structured_evidence(context_items, mission)
    prompt = f"Objective: {decision.objective}\nSelected option: {decision.selected_option}\nEvidence: {evidence}"
    result = await complete_json(
        system=_LLM_SYSTEM, prompt=prompt, task_type=_LLM_TASK_TYPE[intent], max_tokens=300
    )
    if not result or not result.get("summary") or not result.get("reasoning"):
        log.info(f"Decision LLM enhancement unavailable/invalid for intent={intent} — using template")
        return decision

    combined = f"{result['summary']} {result['reasoning']}"
    if _looks_garbled(combined):
        log.warning(
            f"Decision LLM enhancement rejected for intent={intent} — output looks "
            f"garbled/corrupted; using template"
        )
        return decision
    if _asserts_unsupported_relationship(combined, context_items):
        log.warning(
            f"Decision LLM enhancement rejected for intent={intent} — asserted a "
            f"goal relationship not present in the data (BUG-M6-01 guard); using template"
        )
        return decision
    if _asserts_wrong_mission(combined, mission, context_items):
        log.warning(
            f"Decision LLM enhancement rejected for intent={intent} — conflated a Goal "
            f"with the Mission; using template"
        )
        return decision
    if _asserts_wrong_status(combined, context_items):
        log.warning(
            f"Decision LLM enhancement rejected for intent={intent} — called a goal "
            f"'active' when its real status says otherwise; using template"
        )
        return decision

    return decision.model_copy(update={"summary": result["summary"], "reasoning": result["reasoning"]})


async def gather_grounded_evidence(mission) -> tuple[list, str]:
    """Shared evidence-gathering: retrieval + scoring + budget + structured
    JSON evidence — the exact same pipeline reason() below uses, exposed
    here for conversation.api's free-form grounded synthesis
    (_grounded_reply) to reuse directly.

    Deliberately a single, unified retrieval — no Planning/Reflection
    branch — reusing reflection_policy's superset (every goal status, not
    just Active/Draft) since a free-form conversational answer has to
    handle backward- and forward-looking questions alike without a
    hardcoded intent classification sitting upstream of it. See
    ARCHITECTURE_ISSUES.md's entry on replacing the rigid Planning/
    Reflection Decision templates with real conversation for why this
    exists as its own function rather than being folded into reason()
    below — reason() and its Decision/Plan/Action pipeline are left fully
    intact (still real, tested, callable) for anything that later needs
    a structured, action-triggering Decision; this is for the common
    case of just answering a grounded question well.

    2026-08-27 dogfooding bug: this passed `mission.id` (the current
    Mission VERSION row's own id) instead of `mission.identity_id` (the
    stable identity that persists across versions, and the field
    goals.api.create_goal actually stores on every Goal — see
    goals/api.py's create_goal). For a mission's first-ever version
    these happen to be equal (mission.api.set_mission sets
    identity_id = id on creation), which is exactly why this went
    unnoticed through the entire test suite and the first two rounds of
    dogfooding — nothing had superseded a mission yet. The instant a
    mission is superseded (V1-M2's natural-language mission-change
    confirmation flow, or the pre-existing /mission CLI command — this
    bug predates M2 entirely and isn't specific to it), the new version
    gets a fresh `id` while `identity_id` stays constant, so
    reflection_policy(mission.id) silently matched zero goals from that
    point on. Symptom: "list my goals" genuinely, faithfully reported
    zero goals — the model wasn't hallucinating, the evidence handed to
    it really was empty. reason() below already does this correctly
    (RETRIEVAL_POLICIES[intent](mission.identity_id)) — this function
    just didn't match it.
    """
    candidates = await reflection_policy(mission.identity_id)
    scored = score_and_rank(candidates)
    cfg = load_config()
    budget = cfg.get("context.default_budget_tokens", 8000)
    context_items = apply_budget(scored, budget)
    evidence = _build_structured_evidence(context_items, mission)
    return context_items, evidence


async def reason(intent: str, objective: str) -> Decision:
    if intent not in RETRIEVAL_POLICIES:
        raise UnknownIntentError(
            f"No retrieval policy for intent '{intent}'. Available: {list(RETRIEVAL_POLICIES)}"
        )

    mission = await get_active_mission()
    if mission is None:
        raise NoActiveMissionError("Cannot reason without an active Mission.")

    candidates = await RETRIEVAL_POLICIES[intent](mission.identity_id)
    scored = score_and_rank(candidates)

    cfg = load_config()
    budget = cfg.get("context.default_budget_tokens", 8000)
    context_items = apply_budget(scored, budget)

    decision = _DECISION_BUILDERS[intent](objective, context_items, mission.id)
    decision = await _enhance_with_llm(decision, intent, context_items, mission)

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
            intent, decision.created_at, decision.version,
        )

    log.info(f"Decision for intent={intent}: '{decision.summary}' (confidence={decision.confidence})")
    return decision
