"""
Score -> Rank -> Budget Cutoff (CE-05, CE-10, §6.4's ranking pipeline) —
reasoning/ M3.

Per architect guidance (2026-08-02): the simplest correct formula that
satisfies the contract, not a tuned or learned one. Fixed weights, no
caching, no per-intent budget sophistication yet — a flat default from
config. Revisit only once retrieval quality is a measured, real
bottleneck (§5.3 already defers the more sophisticated version of this,
vector search, for exactly that reason).
"""
from __future__ import annotations

from datetime import datetime, timezone

from contracts.context_item import ContextItem
from contracts.enums import Priority
from reasoning.policies import Candidate

# Fixed, documented, deliberately unsophisticated. See module docstring.
_RELEVANCE_WEIGHT = 0.5
_FRESHNESS_WEIGHT = 0.3
_CONFIDENCE_WEIGHT = 0.2
_FRESHNESS_DECAY_DAYS = 30  # items older than this get freshness = 0


def _freshness(updated_at: datetime) -> float:
    """Simple linear decay — not exponential, not tuned. An item updated
    today scores 1.0; one untouched for _FRESHNESS_DECAY_DAYS or more
    scores 0.0; linear in between."""
    now = datetime.now(timezone.utc)
    age_days = (now - updated_at).total_seconds() / 86400
    return max(0.0, 1.0 - (age_days / _FRESHNESS_DECAY_DAYS))


def _priority_for(score: float) -> Priority:
    if score >= 0.8:
        return Priority.HIGH
    if score >= 0.5:
        return Priority.MEDIUM
    return Priority.LOW


def score_and_rank(candidates: list[Candidate]) -> list[tuple[ContextItem, float]]:
    """Retrieve already happened (policies.py). This does Score -> Rank:
    returns (ContextItem, combined_score) pairs sorted highest-score
    first. Budget Cutoff happens separately in apply_budget()."""
    scored: list[tuple[ContextItem, float]] = []

    for c in candidates:
        freshness = _freshness(c.updated_at)
        combined = (
            _RELEVANCE_WEIGHT * c.base_relevance
            + _FRESHNESS_WEIGHT * freshness
            + _CONFIDENCE_WEIGHT * c.confidence
        )
        combined = max(0.0, min(1.0, combined))

        item = ContextItem(
            source_type=c.source_type,
            source_id=c.source_id,
            payload=c.payload,
            relevance_score=c.base_relevance,
            confidence=c.confidence,
            freshness=freshness,
            priority=_priority_for(combined),
            reasoning=c.reasoning,
        )
        scored.append((item, combined))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def _estimate_tokens(item: ContextItem) -> int:
    """Rough word-count heuristic, not a real tokenizer — a real
    tokenizer is exactly the kind of precision this stage doesn't need
    yet. ~1.3 tokens per word is a commonly-used rough English estimate."""
    text = " ".join(str(v) for v in item.payload.values())
    word_count = len(text.split()) + len(item.reasoning.split())
    return int(word_count * 1.3)


def apply_budget(scored: list[tuple[ContextItem, float]], budget_tokens: int) -> list[ContextItem]:
    """Budget Cutoff: greedily take highest-scored items until the budget
    is spent (CE-10, CE-06's Minimal Context Principle). No per-intent or
    per-LLM dynamic sizing yet — budget_tokens comes from a flat config
    default (context.default_budget_tokens)."""
    selected: list[ContextItem] = []
    spent = 0
    for item, _score in scored:
        cost = _estimate_tokens(item)
        if spent + cost > budget_tokens:
            continue  # skip, don't just stop — a smaller lower-ranked item might still fit
        selected.append(item)
        spent += cost
    return selected
