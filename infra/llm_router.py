"""
LLM Router (INF-05/06/07/08; §7 infra/llm_router.py). Every subsystem
calls complete()/complete_json() — nothing talks to a provider SDK
directly (INF-05: "Everything goes through Reason()").

Routing is fixed/rule-based per task category (§5.1 — NOT the adaptive,
learned routing §5.3 defers to INF-V1): each task category has an
ordered chain of model candidates, tried in order with capability
checks, bounded retry, error classification, and health/cooldown
tracking, ending in an OpenRouter free-model emergency pool. Candidate
chains live in config/default.yaml under `llm.routes` — adding or
swapping a model is a config edit, not a code change (§9).

Callers keep using the same three task_type names as before ("fast",
"conversation", "complex") — see _TASK_ALIASES. "coding" is a new,
available task_type for future callers; no current call site needs it.

Model availability verified against provider docs 2026-08-14 — see
ARCHITECTURE_ISSUES.md's 2026-08-14 entry for the summary. Notably:
llama-3.1-8b-instant and llama-3.3-70b-versatile are mid-shutdown on
Groq (announced 2026-06-17, shuts down 2026-08-16) and have been
replaced with Groq's own recommended migration targets
(openai/gpt-oss-20b, qwen/qwen3.6-27b).

Requires local verification: no provider is reachable from the build
sandbox's network allowlist. Logic (routing, retry, fallback, health,
JSON parsing) is tested against mocked HTTP calls in
tests/test_llm_router.py; real-provider correctness has not been
verified. See ARCHITECTURE_ISSUES.md.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, NamedTuple, Optional
import os

import httpx

from config.loader import load_config
from infra.logging import get_logger

log = get_logger("infra.llm_router")

_TIMEOUT = httpx.Timeout(15.0)
_MAX_ATTEMPTS_PER_CANDIDATE = 2

# 2026-08-24: shared by every provider's truncation-retry (see
# GroqProvider.call's fix comment for the full story — raising a
# caller's max_tokens once already proved unreliable; detecting real
# truncation and retrying automatically is the actual fix). One bounded
# retry at a larger budget, capped so a persistently verbose response
# can't runaway the request cost/latency.
_TRUNCATION_RETRY_MULTIPLIER = 2
_TRUNCATION_RETRY_CEILING = 4000


def _truncation_retry_allowed(original_max_tokens: int) -> bool:
    return original_max_tokens < _TRUNCATION_RETRY_CEILING


def _next_truncation_retry_budget(original_max_tokens: int) -> int:
    return min(original_max_tokens * _TRUNCATION_RETRY_MULTIPLIER, _TRUNCATION_RETRY_CEILING)
_RETRY_BASE_DELAY_SECONDS = 1.0

# Cooldown after a failure, before a (provider, model) is eligible again.
# Backs off with recent_failures, capped, so one blip doesn't cost much
# but a persistently broken candidate is not hammered (§6).
_COOLDOWN_BASE_SECONDS = 20.0
_COOLDOWN_CAP_SECONDS = 600.0
# Auth/not-configured failures don't self-heal on a short timer (a bad
# key stays bad), but must not be permanent — a human fixing the .env
# should see it recover within a session, not require a restart.
_AUTH_COOLDOWN_SECONDS = 900.0

_TASK_ALIASES = {"fast": "chat", "conversation": "chat", "complex": "hard"}

# Fallback used only if config/default.yaml is missing the llm.routes
# section (shouldn't happen in this repo, but the module must not be
# unusable without it — same defensive stance as config/loader.py).
_DEFAULT_ROUTES: dict[str, list[dict]] = {
    "chat": [
        {"provider": "gemini", "model": "gemini-3.5-flash-lite", "capabilities": ["text", "json"]},
        {"provider": "groq", "model": "openai/gpt-oss-20b", "capabilities": ["text", "json", "tool_calling"]},
    ],
    "hard": [
        {"provider": "gemini", "model": "gemini-3.6-flash", "capabilities": ["text", "json", "tool_calling", "long_context", "vision"]},
        {"provider": "groq", "model": "openai/gpt-oss-120b", "capabilities": ["text", "json", "tool_calling", "reasoning"]},
        {"provider": "groq", "model": "qwen/qwen3.6-27b", "capabilities": ["text", "json", "vision", "reasoning"]},
    ],
    "coding": [
        {"provider": "openrouter", "model": "qwen/qwen3-coder:free", "capabilities": ["text", "json", "tool_calling", "long_context"]},
        {"provider": "gemini", "model": "gemini-3.6-flash", "capabilities": ["text", "json", "tool_calling", "long_context", "vision"]},
        {"provider": "groq", "model": "openai/gpt-oss-120b", "capabilities": ["text", "json", "tool_calling", "reasoning"]},
    ],
}
_DEFAULT_EMERGENCY = {"provider": "openrouter", "model": "openrouter/free", "capabilities": ["text", "json"]}


class LLMUnavailableError(Exception):
    pass


class ProviderNotConfiguredError(LLMUnavailableError):
    """Raised when a provider's API key isn't set — retrying won't help,
    so this is classified the same as an auth failure: skip straight to
    the next candidate instead of sleeping between attempts that can
    never succeed."""
    pass


class ErrorCategory:
    """How a failure should affect fallback behavior (§4)."""
    RETRYABLE = "retryable"      # 429/408/5xx/timeout/network — bounded retry, then next candidate
    AUTH = "auth"                # 401/403/not-configured — provider-specific, skip to next candidate
    BAD_REQUEST = "bad_request"  # 400/404/422 — not provider-specific; cycling models won't fix a
                                  # malformed request, so surface it instead of hiding it (§4)
    UNKNOWN = "unknown"


def classify_error(exc: Exception) -> str:
    if isinstance(exc, ProviderNotConfiguredError):
        return ErrorCategory.AUTH
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429 or status in (408, 500, 502, 503, 504):
            return ErrorCategory.RETRYABLE
        if status in (401, 402, 403):
            # 402 added 2026-09-08: CodeCraft API (and any other
            # balance-metered provider) returns 402 insufficient_funds
            # once a monthly free-tier token allowance is exhausted —
            # that won't resolve by retrying within the session, same
            # "not provider's fault, but not coming back soon either"
            # shape as an auth failure, so it gets the same treatment
            # (skip to next candidate, longer cooldown) rather than the
            # short exponential-backoff UNKNOWN would otherwise give it.
            return ErrorCategory.AUTH
        if status in (400, 404, 422):
            return ErrorCategory.BAD_REQUEST
        return ErrorCategory.UNKNOWN
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return ErrorCategory.RETRYABLE
    if isinstance(exc, LLMUnavailableError):
        # e.g. the empty/null-content soft failure below — HTTP succeeded
        # but the response was unusable; worth one retry, not permanent.
        return ErrorCategory.RETRYABLE
    return ErrorCategory.UNKNOWN


# --------------------------------------------------------------------------
# Provider abstraction (§3). Jarvis's core logic never sees Gemini/Groq/
# OpenRouter-specific shapes — only ModelCandidate + these three `call()`
# implementations know the request/response formats.
# --------------------------------------------------------------------------

class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def call(self, model: str, system: str, prompt: str, max_tokens: int) -> str:
        ...


def _with_body_on_error(resp: httpx.Response) -> None:
    # The default httpx error drops the response body, so a 404 just says
    # "404 Not Found" with no hint why (2026-08-11 finding: this cost real
    # debugging time tracing an OpenRouter 404 back to an account-level
    # privacy-setting requirement for free models). Surface the body.
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise httpx.HTTPStatusError(f"{e} — {resp.text[:300]}", request=e.request, response=e.response) from None


class GroqProvider(LLMProvider):
    name = "groq"

    async def call(self, model: str, system: str, prompt: str, max_tokens: int) -> str:
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise ProviderNotConfiguredError("GROQ_API_KEY not set")

        async def _request(tokens: int) -> dict:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                        "max_tokens": tokens,
                    },
                )
                _with_body_on_error(resp)
                return resp.json()

        data = await _request(max_tokens)
        choice = data["choices"][0]
        content = choice["message"]["content"]

        # Fix (2026-08-24): live dogfooding of the grounded-conversation
        # path (see ARCHITECTURE_ISSUES.md) showed genuine multi-step
        # answers cut off mid-sentence repeatedly even after max_tokens
        # was raised once already — raising the number again would just
        # be the same guess, unreliable in the same way. The real gap:
        # nothing anywhere checked whether a response was actually
        # truncated; complete() and every caller only ever saw bare
        # text with no signal either way. finish_reason == "length" is
        # the standard OpenAI-compatible signal for exactly this — Groq
        # uses that shape. One bounded retry at a larger budget, capped
        # so a persistently verbose model can't runaway the request.
        #
        # Fix (2026-08-31, ported from the parallel V1-M5 session ahead
        # of merge — see V1_M6_IMPLEMENTATION_RECORD.md): this retry
        # must run BEFORE the empty-content check below, not after —
        # reasoning models (gpt-oss-20b/120b) can spend the entire
        # max_tokens budget on hidden reasoning and leave `content`
        # completely empty with finish_reason=="length". That's the
        # exact same truncation this block exists to retry, just at the
        # extreme (0 output tokens instead of a partial one). With the
        # empty-content check running first, this retry was unreachable
        # for precisely the case it was built for — confirmed against
        # Groq's own docs: gpt-oss-20b/120b default to
        # reasoning_effort="medium" and route reasoning tokens through a
        # separate `reasoning` field, consuming from the same max_tokens
        # budget as `content`.
        if choice.get("finish_reason") == "length" and _truncation_retry_allowed(max_tokens):
            retried = await _request(_next_truncation_retry_budget(max_tokens))
            retried_content = retried["choices"][0]["message"]["content"]
            if retried_content:
                content = retried_content

        if not content:
            # Fix (§6.5, 2026-08-11): a soft failure — HTTP 200 but
            # null/empty content — must not propagate as a real string;
            # treat it as a provider failure so the retry/fallback
            # chain gets a chance to recover. (Reached only after the
            # truncation retry above has already had its shot — see the
            # 2026-08-31 note above for why the order matters.)
            raise LLMUnavailableError(f"groq/{model} returned empty/null content")
        return content


class GeminiProvider(LLMProvider):
    name = "gemini"

    # BUG-fix history (kept for future maintainers — this endpoint has
    # moved fast in 2026, expect to revisit again):
    #   2026-08-05: gemini-1.5-flash fully retired -> switched to
    #     gemini-2.5-flash on the legacy generateContent endpoint.
    #   2026-08-06 #1: still 404'd. Root cause: Google migrated keys to a
    #     new "AQ." format that isn't accepted via `?key=` query param —
    #     needs the `x-goog-api-key` header. Switched to the header.
    #   2026-08-06 #2: STILL 404'd even with the header fix. Root cause
    #     this time: gemini-2.5-flash is now itself deprecated
    #     (alongside gemini-2.0-*/1.5-*), and Google's GA-recommended
    #     endpoint since June 2026 is the new Interactions API
    #     (/v1beta/interactions), not legacy generateContent — different
    #     URL, different request/response shape. Migrated to that.
    #   2026-08-14 (this pass): re-verified against ai.google.dev — both
    #     endpoints remain valid (generateContent is "legacy but fully
    #     supported"; Interactions API is GA and recommended since June
    #     2026), and the response `steps` schema parsed below matches
    #     the Interactions API's current (post-May-2026-breaking-change)
    #     shape, so no further change needed here — only the model IDs
    #     moved (gemini-3.5-flash -> gemini-3.6-flash for Hard/Coding,
    #     gemini-3.5-flash-lite for Chat; both GA as of 2026-07-21).
    async def call(self, model: str, system: str, prompt: str, max_tokens: int) -> str:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ProviderNotConfiguredError("GEMINI_API_KEY not set")

        async def _request(tokens: int) -> str:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    "https://generativelanguage.googleapis.com/v1beta/interactions",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "input": f"{system}\n\n{prompt}",
                        "generation_config": {"max_output_tokens": tokens},
                    },
                )
                _with_body_on_error(resp)
                data = resp.json()
                # Response shape: {"id": ..., "steps": [{"type": "model_output",
                # "content": [{"type": "text", "text": "..."}]}]}
                texts = [
                    part["text"]
                    for step in data.get("steps", [])
                    if step.get("type") == "model_output"
                    for part in step.get("content", [])
                    if part.get("type") == "text"
                ]
                return "".join(texts)

        text = await _request(max_tokens)
        if not text:
            raise LLMUnavailableError(f"gemini/{model} returned empty/null content")
        # Fix (2026-08-24): unlike Groq/OpenRouter's standard OpenAI-
        # compatible finish_reason field, this endpoint's schema (see the
        # bug-fix history above — it's moved fast and isn't fully
        # documented here) has no confirmed truncation signal to read.
        # Deliberately not guessing an unverified field name and
        # presenting it as real detection — that would be worse than no
        # detection at all. Using a mechanical heuristic instead: genuine
        # complete natural-language output overwhelmingly ends in
        # sentence-terminal punctuation; text that doesn't is very likely
        # cut off mid-thought. One bounded retry at a larger budget, same
        # cap as the other two providers.
        if not re.search(r"[.!?\"')\]]\s*$", text) and _truncation_retry_allowed(max_tokens):
            retried_text = await _request(_next_truncation_retry_budget(max_tokens))
            if retried_text:
                text = retried_text
        return text


class OpenRouterProvider(LLMProvider):
    name = "openrouter"

    # Fix (2026-08-14, corrected): the earlier `reasoning.exclude` fix was
    # wrong — that flag only strips *structured* reasoning tokens (models
    # OpenRouter tracks as having a distinct thinking channel, e.g.
    # DeepSeek R1). Some free-tier backends just write "Here's a thinking
    # process: 1. ... 2. ..." as ordinary content — OpenRouter has no
    # separate channel to exclude there, so the flag alone is a no-op
    # against that failure mode. Kept `reasoning.exclude` too since it's
    # free insurance for whichever model actually does have structured
    # reasoning support; the FINAL: marker is the part that reliably works.
    async def call(self, model: str, system: str, prompt: str, max_tokens: int) -> str:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ProviderNotConfiguredError("OPENROUTER_API_KEY not set")
        no_cot_system = (
            f"{system}\n\n"
            "Respond with ONLY your final answer — no narrated reasoning, no "
            "numbered analysis steps, no preamble. Prefix your actual answer "
            "with the exact marker 'FINAL:' on its own line, then write only "
            "the answer after it. Example:\nFINAL:\nYour answer text here."
        )

        async def _request(tokens: int) -> dict:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "system", "content": no_cot_system}, {"role": "user", "content": prompt}],
                        "max_tokens": tokens,
                        "reasoning": {"exclude": True},
                    },
                )
                _with_body_on_error(resp)
                return resp.json()

        data = await _request(max_tokens)
        choice = data["choices"][0]
        content = choice["message"]["content"]
        if not content:
            raise LLMUnavailableError(f"openrouter/{model} returned empty/null content")
        # Fix (2026-08-24) — same truncation-retry as GroqProvider above,
        # same standard finish_reason field (OpenRouter proxies OpenAI-
        # compatible responses).
        if choice.get("finish_reason") == "length" and _truncation_retry_allowed(max_tokens):
            retried = await _request(_next_truncation_retry_budget(max_tokens))
            retried_content = retried["choices"][0]["message"]["content"]
            if retried_content:
                content = retried_content
        if "FINAL:" in content:
            content = content.split("FINAL:", 1)[1].strip()
        return content


class ZaiProvider(LLMProvider):
    """z.ai (GLM models), added 2026-09-08 for the free-tier-resilience
    push — see V1_M5_IMPLEMENTATION_RECORD.md's 2026-09-08 entry.
    Standard OpenAI-compatible chat/completions (verified against
    docs.z.ai 2026-09-08); GLM-5.2/5.3 are metered even on a direct z.ai
    key — only the *-flash tier (glm-4.5/4.6v/4.7-flash) is genuinely
    free, which is what config/default.yaml actually points this at."""
    name = "zai"

    async def call(self, model: str, system: str, prompt: str, max_tokens: int) -> str:
        key = os.environ.get("ZAI_API_KEY")
        if not key:
            raise ProviderNotConfiguredError("ZAI_API_KEY not set")

        async def _request(tokens: int) -> dict:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    "https://api.z.ai/api/paas/v4/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                        "max_tokens": tokens,
                    },
                )
                _with_body_on_error(resp)
                return resp.json()

        data = await _request(max_tokens)
        choice = data["choices"][0]
        content = choice["message"]["content"]
        # Same truncation-retry as GroqProvider — standard OpenAI-shape
        # finish_reason field, and GLM is a reasoning-capable family too
        # (can spend budget on hidden reasoning the same way gpt-oss does).
        if choice.get("finish_reason") == "length" and _truncation_retry_allowed(max_tokens):
            retried = await _request(_next_truncation_retry_budget(max_tokens))
            retried_content = retried["choices"][0]["message"]["content"]
            if retried_content:
                content = retried_content
        if not content:
            raise LLMUnavailableError(f"zai/{model} returned empty/null content")
        return content


class CodeCraftProvider(LLMProvider):
    """CodeCraft API (multi-model reseller), added 2026-09-08 — see
    V1_M5_IMPLEMENTATION_RECORD.md's 2026-09-08 entry. Standard OpenAI-
    compatible chat/completions, verified against codecraftapi.com/docs
    2026-09-08. Free plan is a 1M-token/month allowance, not unlimited —
    once exhausted the API returns 402 insufficient_funds, which
    classify_error() now maps to ErrorCategory.AUTH (see that change's
    2026-09-08 comment) so the router skips this candidate for a real
    cooldown period instead of retrying a quota that won't refill until
    next month."""
    name = "codecraft"

    async def call(self, model: str, system: str, prompt: str, max_tokens: int) -> str:
        key = os.environ.get("CODECRAFT_API_KEY")
        if not key:
            raise ProviderNotConfiguredError("CODECRAFT_API_KEY not set")

        async def _request(tokens: int) -> dict:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    "https://codecraftapi.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                        "max_tokens": tokens,
                    },
                )
                _with_body_on_error(resp)
                return resp.json()

        data = await _request(max_tokens)
        choice = data["choices"][0]
        content = choice["message"]["content"]
        if choice.get("finish_reason") == "length" and _truncation_retry_allowed(max_tokens):
            retried = await _request(_next_truncation_retry_budget(max_tokens))
            retried_content = retried["choices"][0]["message"]["content"]
            if retried_content:
                content = retried_content
        if not content:
            raise LLMUnavailableError(f"codecraft/{model} returned empty/null content")
        return content


class GitHubModelsProvider(LLMProvider):
    """GitHub Models (models.github.ai — the free "market" catalog, not
    the separate api.githubcopilot.com Copilot-only catalog), added
    2026-09-08 — see V1_M5_IMPLEMENTATION_RECORD.md's 2026-09-08 entry.
    Standard OpenAI-compatible chat/completions; auth is any GitHub
    personal access token with the `models` scope, not a
    service-specific key."""
    name = "github"

    async def call(self, model: str, system: str, prompt: str, max_tokens: int) -> str:
        key = os.environ.get("GITHUB_TOKEN")
        if not key:
            raise ProviderNotConfiguredError("GITHUB_TOKEN not set")

        async def _request(tokens: int) -> dict:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    "https://models.github.ai/inference/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                        "max_tokens": tokens,
                    },
                )
                _with_body_on_error(resp)
                return resp.json()

        data = await _request(max_tokens)
        choice = data["choices"][0]
        content = choice["message"]["content"]
        if choice.get("finish_reason") == "length" and _truncation_retry_allowed(max_tokens):
            retried = await _request(_next_truncation_retry_budget(max_tokens))
            retried_content = retried["choices"][0]["message"]["content"]
            if retried_content:
                content = retried_content
        if not content:
            raise LLMUnavailableError(f"github/{model} returned empty/null content")
        return content


def _provider_registry() -> dict[str, LLMProvider]:
    # Built fresh per call, not at module load — resolves the current
    # module-global classes, so test mocks (patch.object(GroqProvider,
    # "call", ...)) actually take effect rather than freezing references
    # to the original bound methods at import time.
    return {
        "groq": GroqProvider(), "gemini": GeminiProvider(), "openrouter": OpenRouterProvider(),
        "zai": ZaiProvider(), "codecraft": CodeCraftProvider(), "github": GitHubModelsProvider(),
    }


# --------------------------------------------------------------------------
# Candidates, routes, capabilities (§8, §9)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model: str
    capabilities: frozenset[str] = field(default_factory=lambda: frozenset({"text", "json"}))

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider, self.model)


def _candidate_from_dict(d: dict) -> ModelCandidate:
    return ModelCandidate(provider=d["provider"], model=d["model"], capabilities=frozenset(d.get("capabilities", ["text", "json"])))


@lru_cache(maxsize=1)
def _routes() -> tuple[dict[str, list[ModelCandidate]], ModelCandidate]:
    """Loads llm.routes / llm.emergency_fallback from config, falling back
    to the hardcoded defaults above if config is missing or malformed —
    the router must stay usable even without config wiring, same
    defensive stance config/loader.py takes with its own file. Cached:
    config is static for the process lifetime."""
    try:
        cfg = load_config()
        raw_routes = cfg.get("llm.routes") or _DEFAULT_ROUTES
        raw_emergency = cfg.get("llm.emergency_fallback") or _DEFAULT_EMERGENCY
        routes = {task: [_candidate_from_dict(c) for c in chain] for task, chain in raw_routes.items()}
        emergency = _candidate_from_dict(raw_emergency)
        return routes, emergency
    except Exception as e:  # noqa: BLE001 — config problems must not make the router unusable
        log.info(f"llm routing config unavailable/invalid ({e}); using built-in defaults")
        routes = {task: [_candidate_from_dict(c) for c in chain] for task, chain in _DEFAULT_ROUTES.items()}
        return routes, _candidate_from_dict(_DEFAULT_EMERGENCY)


class _Attempt(NamedTuple):
    candidate: ModelCandidate
    is_emergency: bool


def _candidates_for(route_name: str) -> list[_Attempt]:
    routes, emergency = _routes()
    chain = routes.get(route_name, [])
    attempts = [_Attempt(c, False) for c in chain]
    if emergency.key not in {a.candidate.key for a in attempts}:
        attempts.append(_Attempt(emergency, True))
    return attempts


# --------------------------------------------------------------------------
# Health / cooldown tracking (§6)
# --------------------------------------------------------------------------

@dataclass
class _Health:
    recent_failures: int = 0
    cooldown_until: float = 0.0
    last_success: Optional[float] = None
    last_error: Optional[str] = None
    error_type: Optional[str] = None


_health: dict[tuple[str, str], _Health] = {}


def _health_for(key: tuple[str, str]) -> _Health:
    return _health.setdefault(key, _Health())


def _is_healthy(key: tuple[str, str]) -> bool:
    h = _health.get(key)
    return h is None or time.monotonic() >= h.cooldown_until


def _record_failure(key: tuple[str, str], category: str, message: str) -> None:
    h = _health_for(key)
    h.recent_failures += 1
    h.last_error = message[:300]
    h.error_type = category
    if category == ErrorCategory.AUTH:
        h.cooldown_until = time.monotonic() + _AUTH_COOLDOWN_SECONDS
    else:
        delay = min(_COOLDOWN_BASE_SECONDS * (2 ** (h.recent_failures - 1)), _COOLDOWN_CAP_SECONDS)
        h.cooldown_until = time.monotonic() + delay


def _record_success(key: tuple[str, str]) -> None:
    h = _health_for(key)
    h.recent_failures = 0
    h.cooldown_until = 0.0
    h.last_success = time.monotonic()
    h.last_error = None
    h.error_type = None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

async def complete(
    prompt: str,
    system: str = "",
    task_type: str = "conversation",
    max_tokens: int = 500,
    capabilities: Optional[set[str]] = None,
) -> str:
    """Tries each candidate in the task's fallback chain in order (config-
    driven, §9), then an OpenRouter free-model emergency pool. Within a
    candidate: bounded retry with backoff for transient errors only
    (§4); auth/not-configured errors skip straight to the next candidate;
    a bad-request error is surfaced immediately rather than cycling
    through unrelated models, since a malformed request fails the same
    way everywhere (§4). Candidates missing a required capability, or
    currently in cooldown from repeated failures (§6), are skipped —
    unless every candidate is unhealthy, in which case the chain is
    tried anyway rather than failing outright. Callers decide what to do
    on total failure (V0's three replaced placeholders fall back to
    their old deterministic templates — see reasoning/orchestrator/insight)."""
    route_name = _TASK_ALIASES.get(task_type, task_type)
    required = frozenset(capabilities) if capabilities else frozenset()

    all_attempts = [a for a in _candidates_for(route_name) if required.issubset(a.candidate.capabilities)]
    if not all_attempts:
        raise LLMUnavailableError(
            f"No configured candidate for task_type={task_type} satisfies required capabilities {sorted(required)}"
        )

    healthy_attempts = [a for a in all_attempts if _is_healthy(a.candidate.key)]
    ordered = healthy_attempts or all_attempts  # don't hard-fail just because everything's cooling down

    last_error: Optional[Exception] = None
    providers = _provider_registry()

    for attempt in ordered:
        candidate = attempt.candidate
        key = candidate.key
        provider = providers[candidate.provider]

        for n in range(_MAX_ATTEMPTS_PER_CANDIDATE):
            t0 = time.monotonic()
            try:
                result = await provider.call(candidate.model, system, prompt, max_tokens)
            except Exception as e:  # noqa: BLE001 — classify below; transient provider failures are expected
                latency = time.monotonic() - t0
                category = classify_error(e)
                last_error = e
                _record_failure(key, category, str(e))
                log.info(
                    f"task_type={route_name} emergency={attempt.is_emergency} "
                    f"provider={candidate.provider} model={candidate.model} attempt={n + 1} "
                    f"result=error category={category} latency={latency:.2f}s fallback=next-candidate error={e}"
                )
                if category == ErrorCategory.BAD_REQUEST:
                    # Not provider-specific — surface it instead of hiding
                    # a real bug behind a wall of unrelated fallbacks.
                    raise LLMUnavailableError(f"Bad request to {candidate.provider}/{candidate.model}: {e}") from e
                if category == ErrorCategory.RETRYABLE and n < _MAX_ATTEMPTS_PER_CANDIDATE - 1:
                    await asyncio.sleep(_RETRY_BASE_DELAY_SECONDS * (2 ** n))
                    continue
                break  # AUTH, exhausted RETRYABLE, or UNKNOWN -> next candidate
            else:
                latency = time.monotonic() - t0
                _record_success(key)
                log.info(
                    f"task_type={route_name} emergency={attempt.is_emergency} "
                    f"provider={candidate.provider} model={candidate.model} attempt={n + 1} "
                    f"result=success latency={latency:.2f}s"
                )
                return result

    raise LLMUnavailableError(f"All providers failed for task_type={task_type}: {last_error}")


async def complete_json(
    system: str,
    prompt: str,
    task_type: str = "conversation",
    max_tokens: int = 500,
    capabilities: Optional[set[str]] = None,
) -> Optional[dict[str, Any]]:
    """Same as complete(), but parses a JSON object out of the response
    and returns None (never raises) on any failure — the caller's signal
    to use its deterministic fallback."""
    try:
        text = await complete(prompt=prompt, system=system, task_type=task_type, max_tokens=max_tokens, capabilities=capabilities)
    except LLMUnavailableError as e:
        log.info(f"complete_json: no provider available ({e}); caller should fall back")
        return None

    if not text:
        # Defense-in-depth: provider implementations now raise
        # LLMUnavailableError on empty/null content themselves (§6.5), so
        # this shouldn't be reachable — kept as a second guard rather than
        # trusting every current and future provider to always validate.
        log.info("complete_json: provider returned empty text; caller should fall back")
        return None

    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Found in dogfooding, 2026-08-07: Gemini sometimes wraps the JSON in
    # extra prose/bullets rather than a clean ```json fence at the very
    # start, so the strip above misses it. Try extracting the outermost
    # {...} object from anywhere in the text before giving up — still
    # falls back to None (caller's deterministic template) on any
    # further failure, same as before.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    log.info(f"complete_json: response was not valid JSON: {text[:200]!r}")
    return None
