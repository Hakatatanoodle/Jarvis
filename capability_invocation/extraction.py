"""V1-M4: direct structural copy of state_change/extraction.py's pattern
(itself a copy of memory/extraction.py's) — an LLM call proposes a
candidate as strict JSON, fully re-validated in Python before a
CapabilityInvocationCandidate is even constructed, fail-closed to None
on anything malformed. The LLM never writes anything, never resolves an
entity to a real id, and never invents a capability id: it is shown the
real, current `list_capabilities()` output and must copy an id verbatim
from that list, or leave it null.

Deliberately extracts AT MOST ONE invocation per message, same
single-instruction reasoning as state_change/extraction.py's module
docstring.

Entity resolution (goal_ref -> a real Goal id) reuses
state_change.extraction.resolve_goal_ref UNCHANGED — same exact,
case-insensitive-only matching, no fuzzy matching, ambiguous/no-match
handled by the caller (capability_invocation/dispatch.py), never here.

Originally only ever populated goal_ref/note (goals.advance's one
parameter shape). V1-M6 adds path_ref/content for fs.read/fs.write to
the same prompt/validate/candidate shape rather than a redesign — see
contracts/capability_invocation_candidate.py's docstring. path_ref is
raw, as-stated text only (never resolved/authorized here — that's
capability_invocation/dispatch.py's job, deterministic Python only,
per M6_HANDOVER_PROMPT.md's explicit "never trusted to the model's own
judgment"). M5 adds calendar.read_events/calendar.create_event's own
parameter shape (title/start/end/account_ref) to the same
prompt/validate/candidate pattern, same reasoning. account_ref reuses
the exact same verbatim-copy-then-deterministically-resolve contract
as goal_ref (resolve_account_ref below is a direct structural copy of
state_change.extraction.resolve_goal_ref, over a static config list
instead of a DB query — not reused directly, since goal_ref matches
against `(title, id, status)` tuples from Postgres and account_ref
matches against plain strings from config/default.yaml).

Takes recent conversation context for the same reason
state_change/extraction.py does (2026-08-27 fix, ARCHITECTURE_ISSUES.md):
a bare follow-up answering Jarvis's own clarifying question carries no
command language of its own.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from contracts.capability import Capability
from contracts.capability_invocation_candidate import CapabilityInvocationCandidate
from contracts.enums import ParamType
from infra.llm_router import complete_json
from infra.logging import get_logger
from state_change.extraction import resolve_goal_ref

log = get_logger("capability_invocation.extraction")

_MAX_KNOWN_GOALS_SHOWN = 30  # same bound as state_change/extraction.py, same reasoning


def resolve_account_ref(account_ref: str, known_accounts: list[str]) -> list[str]:
    """Exact, case-insensitive match only — same discipline as
    state_change.extraction.resolve_goal_ref, over config/default.yaml's
    static `calendar.accounts` list instead of a DB query. Returns the
    list of matches (0, 1, or >1 — >1 only possible if the config list
    itself has a duplicate nickname, which is a config bug, not
    something this function should paper over by de-duping silently)."""
    return [a for a in known_accounts if a.strip().lower() == account_ref.lower()]


def _looks_like_iso_datetime(value: str) -> bool:
    """Shape check only (extraction never re-derives what "tomorrow at
    noon" means — the LLM does that, given _now_ below as a reference
    point; this just rejects anything that isn't parseable at all
    before it reaches calendar_ops.py's executors, which pass it to the
    MCP tool call verbatim)."""
    try:
        datetime.fromisoformat(value)
        return True
    except ValueError:
        return False

_EXTRACTION_SYSTEM = (
    "You read one user message for a personal AI system that can invoke "
    "registered Capabilities on the user's behalf. Decide whether the "
    "message is an EXPLICIT INSTRUCTION to invoke one of the listed "
    "Capabilities — never infer this from mood, venting, or "
    "conversational drift. If the message is not a clear, explicit "
    "instruction to run one of the listed capabilities, capability_id "
    "must be null.\n\n"
    "capability_id must be copied VERBATIM, character-for-character, "
    "from the list of registered capability ids given below — never "
    "invent an id, never guess which one the user means. If no listed "
    "capability clearly matches, leave capability_id null (this is "
    "treated as no invocation, not a guess).\n\n"
    "goal_ref (only meaningful for a capability that operates on a "
    "Goal, like goals.advance): must also be copied VERBATIM from the "
    "list of existing goal titles given below — never invent a title, "
    "never paraphrase one, never guess which one the user means. If no "
    "listed title clearly matches, or the capability doesn't operate on "
    "a Goal, leave goal_ref null.\n\n"
    "note: an optional short free-text note the user gave about why or "
    "how (e.g. 'reviewed it today') — null if they didn't give one; "
    "never invent one.\n\n"
    "path_ref (only meaningful for a capability that operates on the "
    "filesystem, like fs.read/fs.write): the path the user described, "
    "in their own words, combined with the CURRENT DIRECTORY shown "
    "below if they are navigating relative to it (e.g. current "
    "directory /Users/x/Projects and the user says 'inside that find "
    "the jarvis project, read the README' -> path_ref "
    "'jarvis/README.md'). Never invent a path component the user didn't "
    "say or imply by relative navigation. If the message gives an "
    "absolute-sounding path, use it as stated. Null if the capability "
    "isn't a filesystem one or no path was given.\n\n"
    "content (only meaningful for fs.write): the literal text the user "
    "wants written to the file, exactly as they gave it — never "
    "invented, never summarized. Null if not a write or not given.\n\n"
    "title/start/end (only meaningful for calendar.read_events or "
    "calendar.create_event): title is the event's name for "
    "calendar.create_event (null for calendar.read_events, which has no "
    "title). start/end must both be full ISO 8601 datetimes with a UTC "
    "offset (e.g. 2026-09-01T14:00:00+00:00). The user speaks in THEIR "
    "OWN LOCAL TIME, given below as both the current local date/time and "
    "the UTC offset that local time is at right now — when they say "
    "\"12 pm\"/\"noon\"/\"tomorrow at 3\", that is local time; convert it "
    "using that offset to produce the correct UTC-offset datetime "
    "(e.g. if local time is UTC+05:45 and the user says \"12 pm\", start "
    "= that day's date + \"T12:00:00+05:45\" — do NOT write +00:00 "
    "unless the user's local offset actually is +00:00). Resolve any "
    "relative time (\"tomorrow at noon\", \"next Monday\", \"this week\") "
    "against the current LOCAL date/time given below, not the UTC one; "
    "for calendar.read_events with no explicit range given, default "
    "start to the current moment and end to 7 days later. Never leave "
    "start/end null for either calendar capability if a range can "
    "reasonably be inferred — null start/end for a calendar capability "
    "means Jarvis will have to ask the user to clarify.\n\n"
    "account_ref (only meaningful for a calendar capability, and only "
    "if the user names a specific account, e.g. \"my work calendar\" or "
    "\"personal\"): must be copied VERBATIM from the list of known "
    "account nicknames given below. If the user doesn't specify an "
    "account, leave account_ref null — this is normal and expected, "
    "not an error; it lets Jarvis check every connected account for a "
    "read, or pick automatically for a write.\n\n"
    "extra (generic per-capability fields): some capabilities declare "
    "their own additional named fields below, each with its own type "
    "and description (and, for an enum field, a fixed list of allowed "
    "values you must copy verbatim). Only fill in an extra field that "
    "belongs to the capability you actually selected for capability_id "
    "— ignore fields listed under a different capability entirely. "
    "Leave an extra field out (or null) if the user didn't say anything "
    "relevant to it; never invent a value for it.\n\n"
    "Continuing a clarifying question: you are also shown recent "
    "conversation from this session. If Jarvis's most recent message "
    "there was itself a clarifying question about which capability or "
    "which goal was meant, and the user's CURRENT message answers that "
    "question — even a short answer like \"the exam goal\" with no "
    "other command language — resolve the FULL original instruction by "
    "combining what the user asked for earlier with this answer. "
    "Otherwise treat the current message as a normal, standalone "
    "instruction as described above.\n\n"
    'Respond ONLY with JSON: {"capability_id": string|null, '
    '"goal_ref": string|null, "note": string|null, '
    '"path_ref": string|null, "content": string|null, "title": string|null, '
    '"start": string|null, "end": string|null, "account_ref": string|null, '
    '"extra": {} (object, only the fields listed below for capabilities that '
    'declare any — keys are the field names given below, values matching '
    'their declared type, or omit/null a field you have nothing for), '
    '"confidence": number 0-1}.'
)


def _collect_extra_param_specs(capabilities: list[Capability]) -> dict[str, tuple[str, Any]]:
    """Union of every registered capability's extra_parameters, keyed
    by field name -> (owning capability_id, ParamSpec). Field names are
    assumed unique across capabilities (documented assumption — see
    contracts/capability.py's ParamSpec docstring); if two capabilities
    ever declare the same field name, the later one in `capabilities`
    wins here, same "last write wins" as any dict comprehension would
    give, not a crash — acceptable until a second data point forces a
    real collision policy."""
    specs: dict[str, tuple[str, Any]] = {}
    for c in capabilities:
        for field_name, spec in c.extra_parameters.items():
            specs[field_name] = (c.id, spec)
    return specs


def _describe_extra_param(field_name: str, capability_id: str, spec) -> str:
    choices_note = f" (allowed values: {', '.join(spec.choices)})" if spec.choices else ""
    required_note = "required" if spec.required else "optional"
    return f"- {field_name} (for {capability_id}, {spec.type.value}, {required_note}){choices_note}: {spec.description}"


def _resolve_local_now(now: datetime, user_timezone: str) -> tuple[datetime, str]:
    """Dogfooding fix, 2026-09-08: the ORIGINAL bug here wasn't a bad
    LLM guess — it's that this module never told the model the user's
    local time existed at all, only UTC. "12 pm" with no local anchor
    is ambiguous by construction; no model, however capable, can
    recover a fact it was never given. Returns (aware local datetime,
    the timezone name actually used) — the name differs from
    user_timezone only when that string fails to resolve (typo'd or
    unconfigured), in which case this fails closed to UTC rather than
    raising and losing the whole capability-invocation call."""
    try:
        return now.astimezone(ZoneInfo(user_timezone)), user_timezone
    except (ZoneInfoNotFoundError, ValueError) as e:
        log.warning(f"Unknown user_timezone {user_timezone!r} ({e}) — falling back to UTC")
        return now, "UTC"


def _build_prompt(
    user_text: str,
    capabilities: list[Capability],
    known_goals: list[tuple[str, str, str]],
    known_accounts: list[str],
    now: datetime,
    recent_context: Optional[str] = None,
    current_directory: Optional[str] = None,
    user_timezone: str = "UTC",
) -> str:
    cap_listing = "\n".join(f"- {c.id}: {c.description}" for c in capabilities) or "(no capabilities registered)"
    goal_listing = "\n".join(
        f"- {title} (status: {status})" for title, _id, status in known_goals[:_MAX_KNOWN_GOALS_SHOWN]
    ) or "(no goals exist yet)"
    account_listing = "\n".join(f"- {a}" for a in known_accounts) or "(no calendar accounts configured)"
    recent_block = (
        f"Recent conversation this session (check whether Jarvis's last "
        f"message here was itself a clarifying question about which "
        f"capability/goal/account/time range was meant — see "
        f"instructions above for what to do if so):\n{recent_context}\n\n"
        if recent_context else ""
    )
    directory_line = f"Current directory (for relative fs navigation): {current_directory}\n\n" if current_directory else ""
    extra_specs = _collect_extra_param_specs(capabilities)
    extra_block = (
        "Per-capability extra fields (see 'extra' in the instructions above):\n"
        + "\n".join(_describe_extra_param(name, cap_id, spec) for name, (cap_id, spec) in extra_specs.items())
        + "\n\n"
        if extra_specs else ""
    )
    local_now, effective_timezone = _resolve_local_now(now, user_timezone)
    return (
        f"Registered capabilities (capability_id must be copied verbatim "
        f"from this list, or left null):\n{cap_listing}\n\n"
        f"Existing goal titles (goal_ref must be copied verbatim from "
        f"this list, or left null):\n{goal_listing}\n\n"
        f"Current date/time — UTC: {now.isoformat()}; user's LOCAL date/"
        f"time ({effective_timezone}): {local_now.isoformat()} — this is "
        f"what the user means when they say a time with no timezone "
        f"attached (see 'title/start/end' instructions above).\n\n"
        f"{directory_line}"
        f"Known calendar account nicknames (account_ref must be copied "
        f"verbatim from this list, or left null):\n{account_listing}\n\n"
        f"{extra_block}"
        f"{recent_block}"
        f"User's message: {user_text}"
    )


def _validate_extra(raw_extra: Any, capability: Capability) -> dict[str, Any]:
    """Generic, type-driven validation of the 'extra' object against
    the SELECTED capability's own declared extra_parameters — fields
    belonging to a different capability (or not declared at all) are
    silently dropped, same fail-closed discipline as every other field
    in this module. A required field that's missing/invalid is simply
    left out of the returned dict; dispatch.py's generic clarify-gate
    is what turns a missing required field into a question, not this
    function (same separation _validate() already keeps for goal_ref/
    path_ref elsewhere: extraction validates shape, dispatch decides
    what a missing value means)."""
    if not capability.extra_parameters or not isinstance(raw_extra, dict):
        return {}
    result: dict[str, Any] = {}
    for name, spec in capability.extra_parameters.items():
        value = raw_extra.get(name)
        if value is None:
            continue
        if spec.type == ParamType.STRING:
            if isinstance(value, str) and value.strip():
                result[name] = value.strip()
        elif spec.type == ParamType.ENUM:
            if isinstance(value, str) and value.strip() in (spec.choices or []):
                result[name] = value.strip()
        elif spec.type == ParamType.DATETIME:
            if isinstance(value, str) and _looks_like_iso_datetime(value.strip()):
                result[name] = value.strip()
        elif spec.type == ParamType.BOOL:
            if isinstance(value, bool):
                result[name] = value
    return result


def _validate(raw: dict[str, Any], valid_capability_ids: set[str]) -> Optional[dict[str, Any]]:
    """Fail-closed re-validation — same shape as state_change/extraction.py's
    _validate. capability_id is checked against the REAL, live registry
    ids passed in by the caller, never against anything the LLM claims
    exists."""
    if not isinstance(raw, dict):
        return None
    capability_id = raw.get("capability_id")
    if not isinstance(capability_id, str) or capability_id not in valid_capability_ids:
        return None

    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        confidence = 0.7

    goal_ref = raw.get("goal_ref")
    goal_ref = goal_ref.strip() if isinstance(goal_ref, str) and goal_ref.strip() else None

    note = raw.get("note")
    note = note.strip() if isinstance(note, str) and note.strip() else None

    path_ref = raw.get("path_ref")
    path_ref = path_ref.strip() if isinstance(path_ref, str) and path_ref.strip() else None

    content = raw.get("content")
    content = content if isinstance(content, str) and content != "" else None

    title = raw.get("title")
    title = title.strip() if isinstance(title, str) and title.strip() else None

    start = raw.get("start")
    start = start.strip() if isinstance(start, str) and _looks_like_iso_datetime(start.strip()) else None

    end = raw.get("end")
    end = end.strip() if isinstance(end, str) and _looks_like_iso_datetime(end.strip()) else None

    account_ref = raw.get("account_ref")
    account_ref = account_ref.strip() if isinstance(account_ref, str) and account_ref.strip() else None

    return {
        "capability_id": capability_id, "goal_ref": goal_ref, "note": note,
        "path_ref": path_ref, "content": content,
        "title": title, "start": start, "end": end, "account_ref": account_ref,
        "confidence": confidence,
    }


async def extract_candidate(
    user_text: str,
    capabilities: list[Capability],
    known_goals: Optional[list[tuple[str, str, str]]] = None,
    recent_context: Optional[str] = None,
    current_directory: Optional[str] = None,
    known_accounts: Optional[list[str]] = None,
    now: Optional[datetime] = None,
    user_timezone: str = "UTC",
) -> Optional[CapabilityInvocationCandidate]:
    known_goals = known_goals or []
    known_accounts = known_accounts or []
    now = now or datetime.now(timezone.utc)
    valid_capability_ids = {c.id for c in capabilities}
    if not valid_capability_ids:
        return None  # nothing registered — never call the LLM for a decision it can't act on

    raw = await complete_json(
        system=_EXTRACTION_SYSTEM,
        prompt=_build_prompt(
            user_text, capabilities, known_goals, known_accounts, now,
            recent_context, current_directory, user_timezone,
        ),
        task_type="fast", max_tokens=400,
    )
    if raw is None:
        log.info("Capability-invocation extraction unavailable/malformed — no candidate")
        return None

    validated = _validate(raw, valid_capability_ids)
    if validated is None:
        if raw.get("capability_id") is not None:
            log.info(f"Capability-invocation extraction dropped an invalid payload, discarding: {raw!r}")
        return None

    resolved_goal_id: Optional[str] = None
    match_count = 0
    if validated["goal_ref"] is not None:
        matched_ids, _status = resolve_goal_ref(validated["goal_ref"], known_goals)
        match_count = len(matched_ids)
        resolved_goal_id = matched_ids[0] if match_count == 1 else None

    resolved_account: Optional[str] = None
    account_match_count = 0
    if validated["account_ref"] is not None:
        matched_accounts = resolve_account_ref(validated["account_ref"], known_accounts)
        account_match_count = len(matched_accounts)
        resolved_account = matched_accounts[0] if account_match_count == 1 else None

    selected_capability = next((c for c in capabilities if c.id == validated["capability_id"]), None)
    extra = (
        _validate_extra(raw.get("extra"), selected_capability)
        if selected_capability is not None else {}
    )

    return CapabilityInvocationCandidate(
        capability_id=validated["capability_id"],
        raw_user_text=user_text,
        confidence=validated["confidence"],
        goal_ref=validated["goal_ref"],
        resolved_goal_id=resolved_goal_id,
        match_count=match_count,
        note=validated["note"],
        path_ref=validated["path_ref"],
        content=validated["content"],
        title=validated["title"],
        start=validated["start"],
        end=validated["end"],
        account_ref=validated["account_ref"],
        resolved_account=resolved_account,
        account_match_count=account_match_count,
        extra=extra,
    )
