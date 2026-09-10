"""V1-M3 §6 pipeline, step 2: "Memory candidate detection / structured
extraction." The LLM proposes structured fields; it never writes
anything and its output is fully re-validated in Python before a
MemoryCandidate is even constructed (§6: "The LLM must not write Memory
directly"). Same fail-closed shape as conversation.api._route
and reasoning.api's JSON-extraction calls: complete_json() never raises,
so any None/malformed response just means "no candidates" — never a
guess, never a raised exception reaching the user.

Revision (2026-08-15, post-dogfooding): v1 of this prompt asked a SHAPE
question — "is this a short, single-clause, explicitly-named-category
statement?" — and extracted at most one candidate. Real dogfooding
showed the failure mode directly: a long, future-tense, multi-part
statement of values and aspirations ("I want to be financially free,
have good relationships, stay healthy...") was discarded outright,
while a throwaway one-liner ("call me Yochan") was kept — the opposite
of what a system whose job is to understand someone over years should
do. Shape and worth are different questions, and no amount of added
shape-rules closes that gap — it only ever covers the cases already
thought of. This version asks the worth question directly ("would
knowing this help Jarvis understand this person?") and allows several
candidates from one message, so a dense statement doesn't get
compressed into a single slot or dropped for not fitting one.
Revision (2026-08-16, post-dogfooding): even with worth-based, stable-
title extraction, a real contradiction slipped through live —
"I like girls" was stored as `user's romantic attraction`, and later
"I don't like girls" was extracted as a DIFFERENT title (`user's
romantic or sexual orientation/preferences`) and stored as a second,
contradictory Active memory instead of updating the first. Root cause:
extraction had zero visibility into what titles already existed, so a
correction/restatement had no way to land on the same title twice in a
row — each call invents a title in isolation. This isn't the semantic-
conflict-resolver the input doc explicitly said not to build (that
would be about reconciling two DIFFERENT titles); it's a narrower,
cheaper fix — give extraction the existing titles so it can reuse one
verbatim when a message is obviously a restatement/correction/update of
something already on file. See extract_candidates' known_memories param.
"""
from __future__ import annotations

from typing import Any, Optional

from contracts.enums import Importance, MemoryType
from contracts.memory_candidate import MemoryCandidate, MemoryScope
from infra.llm_router import complete_json
from infra.logging import get_logger

log = get_logger("memory.extraction")

_VALID_TYPES = {t.value for t in MemoryType}
_VALID_SCOPES = {s.value for s in MemoryScope}
_MAX_CANDIDATES = 5  # a generous cap against a pathological/adversarial wall of text, not a target to hit
_MAX_KNOWN_MEMORIES_SHOWN = 20  # keep the prompt bounded regardless of how much is on file

_EXTRACTION_SYSTEM = (
    "You read one user message for a personal AI system whose whole "
    "purpose is to understand this person over years, not just this "
    "conversation. Extract every durable-memory-worthy fact in it — "
    "there can be zero, one, or several. The question for each one is "
    "WORTH, not shape: would knowing this genuinely help Jarvis "
    "understand who this person is, what they want, what they value, "
    "or how they'd like Jarvis to treat them? That includes short "
    "literal statements (a name, a preference) AND longer, looser ones "
    "— a stated dream, a value, what motivates them, a goal for their "
    "life — as long as it's something THEY said about themselves, not "
    "something you're inferring or guessing. A long or emotionally "
    "phrased statement is not automatically less worth remembering than "
    "a short factual one — if anything, a person stating what they're "
    "chasing in life is exactly the kind of thing worth keeping. Do NOT "
    "extract: questions, requests for Jarvis to do something, small "
    "talk with nothing self-descriptive in it, or opinions about topics "
    "unrelated to the user themselves. This specifically INCLUDES "
    "anything about Jarvis itself — its own name, persona, or how it "
    "should refer to itself (e.g. \"I'm naming you Infinity\") is never "
    "a fact about the user and must never be extracted here, no matter "
    "how explicitly stated or how much it resembles a normal Fact/"
    "Preference in shape. This system has no mechanism for that yet, "
    "and every candidate you produce is shown to the user later as "
    "something Jarvis knows ABOUT THEM — a fact about Jarvis's own "
    "identity does not belong in that list and will be actively "
    "misleading if it ends up there.\n\n"
    "For each candidate: type must be exactly one of Preference, Fact, "
    "Skill, Relationship, Constraint — or null if it's genuinely "
    "memory-worthy but doesn't fit any of those (still extract it; "
    "don't force a bad fit).\n\n"
    "title must be a short, STABLE, canonical descriptor of the fact "
    "itself, not the specific value — e.g. \"user's preferred code "
    "editor\", not \"likes VS Code\", or \"what motivates the user\", "
    "not the full sentence they said. This matters: if the user later "
    "restates or updates it, the same canonical title lets Jarvis "
    "update the existing memory instead of creating a duplicate. If one "
    "message contains several distinct worth-remembering things (e.g. "
    "several separate values or goals in one breath), give each its own "
    "candidate with its own distinct title rather than merging them "
    "into one vague one. If a list of things already known about the "
    "user is given below, and this message restates, corrects, updates, "
    "or contradicts one of them, you MUST reuse that EXACT existing "
    "title AND its exact type character-for-character — do not "
    "paraphrase or improve the title, and do not change the type, even "
    "slightly, or the update will silently create a duplicate instead "
    "of correcting the original. Only reuse an existing title/type when "
    "the message is genuinely about that same underlying fact; never "
    "force unrelated content into an existing title just because one "
    "happens to exist.\n\n"
    "value is the specific fact/preference/aspiration stated, in the "
    "user's own terms — don't editorialize or summarize away detail "
    "they actually gave you.\n\n"
    "scope is \"durable\" if this should be remembered beyond this "
    "conversation (a name, a stable preference, a value, a long-term "
    "aspiration, a standing instruction), \"session\" if the user is "
    "only describing something temporary about right now (mood, what "
    "they're doing today) AND did not ask Jarvis to remember it, or "
    "\"ambiguous\" if you genuinely cannot tell. An explicit "
    "\"remember/save/store this\" always means durable. Do not default "
    "to durable when unsure — use ambiguous.\n\n"
    'Respond ONLY with JSON: {"candidates": [{"type": '
    '"Preference"|"Fact"|"Skill"|"Relationship"|"Constraint"|null, '
    '"title": string, "value": string, '
    '"scope": "durable"|"session"|"ambiguous", "confidence": number 0-1}, '
    '...]}. Use an empty list if nothing in the message is worth '
    "remembering."
)


def _build_prompt(user_text: str, known_memories: Optional[list[tuple[str, str, str]]]) -> str:
    if not known_memories:
        return user_text
    listing = "\n".join(
        f"- [{type_}] {title}: {value}" for title, value, type_ in known_memories[:_MAX_KNOWN_MEMORIES_SHOWN]
    )
    return (
        f"Already known about the user (if this message updates or "
        f"corrects one of these, reuse its title AND its bracketed type "
        f"exactly — do not change either, or the update will silently "
        f"create a duplicate instead of correcting the original):\n{listing}\n\n"
        f"User's new message: {user_text}"
    )


def _validate_one(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Pure re-validation of a single candidate entry — every field
    checked against the exact allowed set before anything downstream
    trusts it. Returns None on any deviation (fail closed, per §6). One
    bad entry in the array only drops that entry, not the whole batch —
    see extract_candidates."""
    if not isinstance(raw, dict):
        return None
    title, value = raw.get("title"), raw.get("value")
    if not isinstance(title, str) or not title.strip() or not isinstance(value, str) or not value.strip():
        return None
    type_raw = raw.get("type")
    if type_raw is not None and type_raw not in _VALID_TYPES:
        return None
    if raw.get("scope") not in _VALID_SCOPES:
        return None
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        confidence = 0.7  # extraction succeeded but omitted/malformed confidence — mild default, not a reject
    return {
        "type": type_raw,
        "title": title.strip(),
        "value": value.strip(),
        "scope": raw["scope"],
        "confidence": float(confidence),
    }


async def extract_candidates(
    user_text: str, known_memories: Optional[list[tuple[str, str, str]]] = None
) -> list[MemoryCandidate]:
    raw = await complete_json(
        system=_EXTRACTION_SYSTEM,
        prompt=_build_prompt(user_text, known_memories),
        task_type="fast", max_tokens=500,
    )
    if raw is None or not isinstance(raw.get("candidates"), list):
        log.info("Memory candidate extraction unavailable/malformed — no candidates")
        return []

    candidates: list[MemoryCandidate] = []
    for entry in raw["candidates"][:_MAX_CANDIDATES]:
        validated = _validate_one(entry)
        if validated is None:
            log.info(f"Memory candidate extraction dropped an invalid entry, discarding: {entry!r}")
            continue
        candidates.append(MemoryCandidate(
            type=MemoryType(validated["type"]) if validated["type"] else None,
            title=validated["title"],
            value=validated["value"],
            scope=MemoryScope(validated["scope"]),
            explicit=True,  # the only thing this prompt is asked to extract — see module docstring
            confidence=validated["confidence"],
            importance=Importance.MEDIUM,
            raw_user_text=user_text,
        ))
    return candidates
