"""V1-M3 Decision 3/1: the deterministic policy boundary a MemoryCandidate
must pass through before memory/api.py ever sees it. Nothing here calls
an LLM — that's the whole point (§9 of the V1-M3 input: "the validation/
policy layer must own this decision", not a prompt instruction).

Two independent checks, both Python/regex, both cheap:

1. `assess_sensitivity` — does the candidate's own text look like a
   credential/secret? Runs regardless of what extraction claimed.
2. `decide` — combines sensitivity + scope + taxonomy fit into one of
   four outcomes a caller (conversation/api.py) can act on directly.

Scope kept deliberately narrow (V1-M3 §5 Decision 3, "At minimum..."):
only the explicitly named hard-block categories (passwords, API keys,
private credentials, financial secrets) are detected here. Broader
"highly sensitive personal information" is not attempted — a keyword
list for that is either so broad it misfires constantly or so narrow it
gives false confidence, and V1-M3 doesn't ask for a general PII
classifier. Logged as a scope note in ARCHITECTURE_ISSUES.md rather than
silently guessed at.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from contracts.memory_candidate import MemoryCandidate, MemoryScope, Sensitivity

# Deliberately narrow and literal (see module docstring) — credential-
# shaped nouns, not a general secrecy/PII detector. Checked against
# title, value, and the raw utterance, so "my API key is sk-abc123" is
# caught even if extraction put the key itself only in `value`.
_SENSITIVE_PATTERNS = re.compile(
    r"\b(password|passwd|pwd|api[\s_-]?key|secret[\s_-]?key|access[\s_-]?token|"
    r"auth[\s_-]?token|private[\s_-]?key|credit[\s_-]?card|card number|cvv|"
    r"ssn|social security|bank account|routing number|pin code|seed phrase)\b",
    re.IGNORECASE,
)


def assess_sensitivity(candidate: MemoryCandidate) -> Sensitivity:
    text = f"{candidate.title} {candidate.value} {candidate.raw_user_text}"
    if _SENSITIVE_PATTERNS.search(text):
        return Sensitivity.SENSITIVE
    return Sensitivity.SAFE


class PolicyOutcome(str, Enum):
    WRITE_DURABLE = "write_durable"          # safe, explicit, durable, taxonomy fits -> write immediately
    ASK_CONFIRMATION = "ask_confirmation"      # ambiguous scope, otherwise fine -> ask before writing
    BLOCK_SENSITIVE = "block_sensitive"        # credential-shaped -> never write, not even with confirmation
    TAXONOMY_GAP = "taxonomy_gap"              # doesn't fit the 5 types -> log a gap proposal, don't write
    NO_OP = "no_op"                            # session-scoped or non-explicit -> nothing to do


@dataclass
class PolicyDecision:
    outcome: PolicyOutcome
    reason: str  # human-readable — surfaced verbatim in the CLI confirmation prompt


def decide(candidate: MemoryCandidate) -> PolicyDecision:
    # Deterministic re-check regardless of what extraction set — Decision
    # 3 requires the policy layer to own this, not trust the LLM's field.
    sensitivity = assess_sensitivity(candidate)

    if sensitivity is Sensitivity.SENSITIVE:
        return PolicyDecision(
            PolicyOutcome.BLOCK_SENSITIVE,
            "this looks like a password, API key, or other credential — "
            "I don't store those, even temporarily. Use a password manager for it.",
        )

    if not candidate.explicit:
        # Decision 2: inference is deferred entirely in V1-M3. Should be
        # unreachable in practice (extraction only emits explicit=True
        # candidates) — kept as a hard backstop, not a trusted default.
        return PolicyDecision(PolicyOutcome.NO_OP, "not an explicit statement")

    if not candidate.taxonomy_fit:
        return PolicyDecision(
            PolicyOutcome.TAXONOMY_GAP,
            "worth remembering but doesn't fit Jarvis's current memory "
            "categories yet — flagged for review, not saved",
        )

    if candidate.scope is MemoryScope.SESSION:
        return PolicyDecision(PolicyOutcome.NO_OP, "session-scoped, not durable")

    if candidate.scope is MemoryScope.AMBIGUOUS:
        return PolicyDecision(
            PolicyOutcome.ASK_CONFIRMATION,
            "not sure if you want me to remember this beyond this conversation",
        )

    return PolicyDecision(PolicyOutcome.WRITE_DURABLE, "explicit, safe, durable")
