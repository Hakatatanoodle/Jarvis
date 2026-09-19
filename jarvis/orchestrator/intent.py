"""Intent Detection (OR-02 stage 1 / CE-02). LLM-backed now (real
replacement per architect decision 2026-08-04); falls back to the
original keyword heuristic if no provider is available or responds
usably — same resilience pattern used across reasoning/insight."""
from __future__ import annotations

from infra.llm_router import complete_json
from infra.logging import get_logger

log = get_logger("orchestrator.intent")

_VALID_INTENTS = {"Planning", "Reflection"}
_REFLECTION_KEYWORDS = ("reflect", "how did", "how has", "review", "went", "looking back", "progress so far")

_SYSTEM = (
    "Classify the user's request into exactly one intent: 'Planning' "
    "(deciding what to do next / prioritizing goals) or 'Reflection' "
    "(looking back on recent progress). Respond ONLY with JSON: "
    '{"intent": "Planning"} or {"intent": "Reflection"}.'
)


def _keyword_fallback(user_text: str) -> str:
    lowered = user_text.lower()
    return "Reflection" if any(kw in lowered for kw in _REFLECTION_KEYWORDS) else "Planning"


async def detect_intent(user_text: str) -> str:
    result = await complete_json(system=_SYSTEM, prompt=user_text, task_type="fast", max_tokens=30)
    if result and result.get("intent") in _VALID_INTENTS:
        return result["intent"]
    log.info("Intent LLM call unavailable/invalid — using keyword fallback")
    return _keyword_fallback(user_text)
