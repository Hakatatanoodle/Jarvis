"""V1-M4: transient candidate for a natural-language Capability
invocation ("touch my Build Jarvis goal", "mark that I reviewed the
exam goal").

Own file, same reasoning as contracts/state_change_candidate.py's
docstring: transient (never persisted directly — a validated candidate
becomes a real Action row, but the candidate itself is not a §6
contract), and specific to one pipeline
(capability_invocation/extraction.py + dispatch.py).

Deliberately a flat dataclass with optional fields, same shape
StateChangeCandidate already uses across its three different
operations — this milestone only populates capability_id/goal_ref/note
(goals.advance's one parameter shape), but the same dataclass is meant
to grow new optional fields per capability (M5: start_time/end_time for
calendar; M6: a path for filesystem) rather than being redesigned.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CapabilityInvocationCandidate:
    capability_id: str  # verbatim, re-validated against the live capability registry — never trusted as-is
    raw_user_text: str
    confidence: float = 0.7

    # goals.advance's one parameter shape — same verbatim-goal-title +
    # deterministic-resolution contract state_change/extraction.py
    # already established for goal_ref.
    goal_ref: Optional[str] = None
    resolved_goal_id: Optional[str] = None
    match_count: int = 0  # 0 = no match, 1 = resolved, >1 = ambiguous

    note: Optional[str] = None  # optional free-text note passed through to the capability's parameters

    # V1-M6: fs.read/fs.write's shape. path_ref is the raw, as-stated
    # path text (possibly relative to the session's tracked current
    # directory — see capability_invocation/dispatch.py's fs-navigation
    # section) — NEVER resolved or authorized here; that happens
    # deterministically in Python in dispatch, never trusted to the
    # model. content is the literal text to write, only meaningful for
    # fs.write.
    path_ref: Optional[str] = None
    content: Optional[str] = None

    # M5: calendar.read_events / calendar.create_event's parameter shape.
    # start/end are ISO 8601 strings, passed through to the executor
    # unparsed (calendar_ops.py's executors are the ones that hand them
    # to the MCP tool call verbatim) — extraction only checks they're
    # present and look like ISO datetimes (see extraction.py's
    # _looks_like_iso_datetime), never re-interprets "tomorrow at noon"
    # itself; that resolution happens in the LLM call, same as every
    # other extraction module in this codebase trusts the model for
    # natural-language-to-structured-value conversion but re-validates
    # the *shape*, not the semantics.
    title: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None

    # Same verbatim-copy + deterministic-resolution contract as
    # goal_ref/resolved_goal_id/match_count above, resolved against
    # config/default.yaml's `calendar.accounts` list instead of a DB
    # query. None/0/None (unresolved, no ref given) is the common case
    # and is NOT an error — omitting account is a valid, meaningful
    # choice for calendar-mcp (merge-all for reads, auto-select for
    # writes; see calendar_ops.py).
    account_ref: Optional[str] = None
    resolved_account: Optional[str] = None
    account_match_count: int = 0

    # Generic-parameter mechanism (2026-09-07, additive): values for
    # whatever the selected capability declares in its
    # Capability.extra_parameters (see contracts/capability.py's
    # ParamSpec docstring). Keyed by field name, already type-validated
    # by extraction.py's generic validator against that capability's
    # declared spec — dispatch.py's generic clarify-gate reads this
    # dict rather than a new hardcoded field per capability. Empty for
    # every capability that doesn't declare any extra_parameters
    # (i.e. every capability that existed before this mechanism).
    extra: dict[str, Any] = field(default_factory=dict)
