"""Unit tests for infra/llm_router.py — routing, retry, fallback, health/
cooldown, capability filtering, error classification, and JSON parsing,
all against mocked provider calls (real API verification is
local-testing-required, see ARCHITECTURE_ISSUES.md)."""
import httpx
import pytest
from unittest.mock import AsyncMock, patch

from infra.llm_router import (
    ErrorCategory,
    GeminiProvider,
    GroqProvider,
    OpenRouterProvider,
    LLMUnavailableError,
    ProviderNotConfiguredError,
    classify_error,
    complete,
    complete_json,
)
import infra.llm_router as router_module


def _status_error(status: int, body: str = "boom") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test")
    response = httpx.Response(status, request=request, text=body)
    return httpx.HTTPStatusError(f"{status} error", request=request, response=response)


@pytest.fixture(autouse=True)
def _clean_router_state(monkeypatch):
    """Every test starts with a cold health table, every key set, and no
    real retry-backoff delay (tests assert on call order/args, not on
    wall-clock timing — the one test that asserts on sleep() itself
    installs its own mock inside a narrower `with` block)."""
    router_module._health.clear()
    for var in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(var, "fake-key")
    monkeypatch.setattr(router_module.asyncio, "sleep", AsyncMock())
    yield
    router_module._health.clear()


# --------------------------------------------------------------------------
# Chat route
# --------------------------------------------------------------------------

async def test_chat_primary_gemini_lite_succeeds():
    with patch.object(GeminiProvider, "call", new=AsyncMock(return_value="hi")) as gem, \
         patch.object(GroqProvider, "call", new=AsyncMock()) as groq:
        result = await complete("hi", task_type="chat")
        assert result == "hi"
        gem.assert_called_once()
        assert gem.call_args.args[0] == "gemini-3.5-flash-lite"
        groq.assert_not_called()


async def test_chat_gemini_429_falls_back_to_groq_gpt_oss_20b():
    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(429))), \
         patch.object(GroqProvider, "call", new=AsyncMock(return_value="fallback ok")) as groq:
        result = await complete("hi", task_type="chat")
        assert result == "fallback ok"
        assert groq.call_args.args[0] == "openai/gpt-oss-20b"


async def test_chat_both_fail_raises():
    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(GroqProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(OpenRouterProvider, "call", new=AsyncMock(side_effect=_status_error(500))):
        with pytest.raises(LLMUnavailableError):
            await complete("hi", task_type="chat")


async def test_legacy_task_type_aliases_still_route_correctly():
    # "fast"/"conversation" -> chat, "complex" -> hard: existing callers
    # (orchestrator/intent.py, conversation/api.py, etc.) are untouched.
    with patch.object(GeminiProvider, "call", new=AsyncMock(return_value="ok")) as gem:
        await complete("hi", task_type="fast")
        assert gem.call_args.args[0] == "gemini-3.5-flash-lite"
    router_module._health.clear()
    with patch.object(GeminiProvider, "call", new=AsyncMock(return_value="ok")) as gem:
        await complete("hi", task_type="complex")
        assert gem.call_args.args[0] == "gemini-3.6-flash"


# --------------------------------------------------------------------------
# Hard route
# --------------------------------------------------------------------------

async def test_hard_primary_gemini_succeeds():
    with patch.object(GeminiProvider, "call", new=AsyncMock(return_value="deep")) as gem:
        result = await complete("think", task_type="hard")
        assert result == "deep"
        assert gem.call_args.args[0] == "gemini-3.6-flash"


async def test_hard_gemini_429_falls_back_to_gpt_oss_120b():
    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(429))), \
         patch.object(GroqProvider, "call", new=AsyncMock(return_value="oss ok")) as groq:
        result = await complete("think", task_type="hard")
        assert result == "oss ok"
        assert groq.call_args.args[0] == "openai/gpt-oss-120b"


async def test_hard_gpt_oss_fails_falls_back_to_qwen():
    # gpt-oss-120b is Groq's own recommended replacement for the
    # deprecated llama-3.3-70b-versatile in third position too, so the
    # third rung uses qwen/qwen3.6-27b to avoid repeating the same model.
    groq_calls = []

    async def groq_side_effect(model, *a, **kw):
        groq_calls.append(model)
        if model == "openai/gpt-oss-120b":
            raise _status_error(500)
        return "qwen ok"

    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(429))), \
         patch.object(GroqProvider, "call", new=AsyncMock(side_effect=groq_side_effect)):
        result = await complete("think", task_type="hard")
        assert result == "qwen ok"
        assert groq_calls == ["openai/gpt-oss-120b", "openai/gpt-oss-120b", "qwen/qwen3.6-27b"]


async def test_hard_all_fail_uses_emergency_openrouter_free():
    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(GroqProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(OpenRouterProvider, "call", new=AsyncMock(return_value="emergency ok")) as opr:
        result = await complete("think", task_type="hard")
        assert result == "emergency ok"
        assert opr.call_args.args[0] == "openrouter/free"


async def test_hard_total_failure_including_emergency_raises():
    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(GroqProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(OpenRouterProvider, "call", new=AsyncMock(side_effect=_status_error(500))):
        with pytest.raises(LLMUnavailableError):
            await complete("think", task_type="hard")


# --------------------------------------------------------------------------
# Coding route
# --------------------------------------------------------------------------

async def test_coding_qwen_succeeds():
    with patch.object(OpenRouterProvider, "call", new=AsyncMock(return_value="code ok")) as opr:
        result = await complete("write a function", task_type="coding")
        assert result == "code ok"
        assert opr.call_args.args[0] == "qwen/qwen3-coder:free"


async def test_coding_qwen_fails_falls_back_to_gemini():
    with patch.object(OpenRouterProvider, "call", new=AsyncMock(side_effect=_status_error(429))), \
         patch.object(GeminiProvider, "call", new=AsyncMock(return_value="gemini code ok")) as gem:
        result = await complete("write a function", task_type="coding")
        assert result == "gemini code ok"
        assert gem.call_args.args[0] == "gemini-3.6-flash"


async def test_coding_qwen_and_gemini_fail_falls_back_to_gpt_oss():
    with patch.object(OpenRouterProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(GroqProvider, "call", new=AsyncMock(return_value="oss code ok")) as groq:
        result = await complete("write a function", task_type="coding")
        assert result == "oss code ok"
        assert groq.call_args.args[0] == "openai/gpt-oss-120b"


async def test_coding_all_fail_uses_emergency_fallback():
    with patch.object(OpenRouterProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(GroqProvider, "call", new=AsyncMock(side_effect=_status_error(500))):
        with pytest.raises(LLMUnavailableError):
            # Every OpenRouterProvider.call is patched to the same
            # side_effect, so even the emergency openrouter/free rung
            # fails here — total failure is the correct outcome.
            await complete("write a function", task_type="coding")


# --------------------------------------------------------------------------
# Error classification (§4)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status,expected",
    [
        (429, ErrorCategory.RETRYABLE),
        (408, ErrorCategory.RETRYABLE),
        (500, ErrorCategory.RETRYABLE),
        (503, ErrorCategory.RETRYABLE),
        (401, ErrorCategory.AUTH),
        (403, ErrorCategory.AUTH),
        (400, ErrorCategory.BAD_REQUEST),
        (404, ErrorCategory.BAD_REQUEST),
    ],
)
def test_classify_http_status_errors(status, expected):
    assert classify_error(_status_error(status)) == expected


def test_classify_provider_not_configured_as_auth():
    assert classify_error(ProviderNotConfiguredError("no key")) == ErrorCategory.AUTH


def test_classify_timeout_as_retryable():
    request = httpx.Request("POST", "https://example.test")
    assert classify_error(httpx.TimeoutException("timed out", request=request)) == ErrorCategory.RETRYABLE


def test_classify_network_failure_as_retryable():
    request = httpx.Request("POST", "https://example.test")
    assert classify_error(httpx.ConnectError("dns failed", request=request)) == ErrorCategory.RETRYABLE


async def test_bad_request_surfaces_immediately_without_cycling_models():
    # A 400 is not provider-specific — cycling to Groq/OpenRouter would
    # just repeat the same malformed request. It should raise directly.
    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(400))), \
         patch.object(GroqProvider, "call", new=AsyncMock()) as groq:
        with pytest.raises(LLMUnavailableError):
            await complete("hi", task_type="chat")
        groq.assert_not_called()


async def test_not_configured_skips_straight_to_next_candidate_no_retry_sleep():
    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=ProviderNotConfiguredError("no key"))), \
         patch.object(GroqProvider, "call", new=AsyncMock(return_value="ok")) as groq, \
         patch("infra.llm_router.asyncio.sleep", new=AsyncMock()) as sleep:
        result = await complete("hi", task_type="chat")
        assert result == "ok"
        sleep.assert_not_called()  # AUTH-classified errors don't get a bounded retry/backoff


# --------------------------------------------------------------------------
# Cooldown / health (§6)
# --------------------------------------------------------------------------

async def test_failed_candidate_enters_cooldown_and_is_skipped_next_call():
    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(GroqProvider, "call", new=AsyncMock(return_value="ok")) as groq:
        await complete("hi", task_type="chat")

    # Second call: Gemini is now in cooldown. It must not be attempted
    # again even though it's still first in the chain.
    with patch.object(GeminiProvider, "call", new=AsyncMock()) as gem, \
         patch.object(GroqProvider, "call", new=AsyncMock(return_value="ok again")):
        result = await complete("hi", task_type="chat")
        assert result == "ok again"
        gem.assert_not_called()


async def test_cooldown_expires_and_candidate_becomes_eligible_again(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(router_module.time, "monotonic", lambda: clock["t"])

    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(GroqProvider, "call", new=AsyncMock(return_value="ok")):
        await complete("hi", task_type="chat")

    # Still within cooldown -> skipped.
    with patch.object(GeminiProvider, "call", new=AsyncMock()) as gem, \
         patch.object(GroqProvider, "call", new=AsyncMock(return_value="ok")):
        await complete("hi", task_type="chat")
        gem.assert_not_called()

    # Advance the clock past the cooldown window.
    clock["t"] += 1000.0
    with patch.object(GeminiProvider, "call", new=AsyncMock(return_value="healed")) as gem:
        result = await complete("hi", task_type="chat")
        assert result == "healed"
        gem.assert_called_once()


async def test_all_candidates_unhealthy_still_attempts_rather_than_hard_failing(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(router_module.time, "monotonic", lambda: clock["t"])

    # Drive every chat candidate (including emergency) into cooldown.
    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(GroqProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(OpenRouterProvider, "call", new=AsyncMock(side_effect=_status_error(500))):
        with pytest.raises(LLMUnavailableError):
            await complete("hi", task_type="chat")

    # Without advancing the clock, everything is still "unhealthy" — but
    # the router should try anyway (better than refusing outright) and
    # succeed once a candidate actually works.
    with patch.object(GeminiProvider, "call", new=AsyncMock(return_value="tried anyway")) as gem:
        result = await complete("hi", task_type="chat")
        assert result == "tried anyway"
        gem.assert_called_once()


# --------------------------------------------------------------------------
# Capability compatibility (§8)
# --------------------------------------------------------------------------

async def test_capability_requirement_skips_incapable_candidate():
    # Chat's fallback (gpt-oss-20b) isn't tagged for vision; requiring
    # vision should skip straight past it to the emergency pool (also
    # text/json-only) rather than ever selecting it.
    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(GroqProvider, "call", new=AsyncMock()) as groq, \
         patch.object(OpenRouterProvider, "call", new=AsyncMock()) as opr:
        with pytest.raises(LLMUnavailableError):
            await complete("hi", task_type="chat", capabilities={"vision"})
        groq.assert_not_called()
        opr.assert_not_called()


async def test_no_candidate_satisfies_capability_raises_immediately():
    with patch.object(GeminiProvider, "call", new=AsyncMock()) as gem:
        with pytest.raises(LLMUnavailableError):
            await complete("hi", task_type="chat", capabilities={"code_execution"})
        gem.assert_not_called()


async def test_hard_route_vision_capability_selects_gemini():
    # Gemini 3.6 Flash is tagged for vision on the Hard route; requesting
    # it should still resolve to the normal primary, not skip it.
    with patch.object(GeminiProvider, "call", new=AsyncMock(return_value="ok")) as gem:
        result = await complete("describe this image", task_type="hard", capabilities={"vision"})
        assert result == "ok"
        assert gem.call_args.args[0] == "gemini-3.6-flash"


# --------------------------------------------------------------------------
# complete_json (unchanged parsing behavior, re-verified against the new router)
# --------------------------------------------------------------------------

async def test_complete_json_parses_valid_json():
    with patch.object(GeminiProvider, "call", new=AsyncMock(return_value='{"summary": "ok"}')):
        result = await complete_json(system="s", prompt="p", task_type="chat")
        assert result == {"summary": "ok"}


async def test_complete_json_strips_markdown_fences():
    fenced = '```json\n{"summary": "ok"}\n```'
    with patch.object(GeminiProvider, "call", new=AsyncMock(return_value=fenced)):
        result = await complete_json(system="s", prompt="p", task_type="chat")
        assert result == {"summary": "ok"}


async def test_complete_json_extracts_object_from_surrounding_prose():
    noisy = 'Sure, here is my analysis:\n* High relevance\n{"summary": "ok"}\nHope that helps!'
    with patch.object(GeminiProvider, "call", new=AsyncMock(return_value=noisy)):
        result = await complete_json(system="s", prompt="p", task_type="chat")
        assert result == {"summary": "ok"}


async def test_complete_json_returns_none_on_invalid_json():
    with patch.object(GeminiProvider, "call", new=AsyncMock(return_value="not json at all")):
        result = await complete_json(system="s", prompt="p", task_type="chat")
        assert result is None


async def test_complete_json_returns_none_never_raises_when_unavailable():
    with patch.object(GeminiProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(GroqProvider, "call", new=AsyncMock(side_effect=_status_error(500))), \
         patch.object(OpenRouterProvider, "call", new=AsyncMock(side_effect=_status_error(500))):
        result = await complete_json(system="s", prompt="p", task_type="chat")
        assert result is None


# --- 2026-08-24: truncation-retry, real provider bodies -----------------
# Live dogfooding: genuine multi-step grounded replies kept cutting off
# mid-sentence even after max_tokens was already raised once — that fix
# was a guess with no way to verify it landed. Root cause: nothing
# anywhere checked whether a response was actually truncated. These
# tests exercise the REAL (non-mocked) provider bodies via a mocked
# httpx.AsyncClient.post, unlike every other test in this file which
# mocks provider.call() itself and never touches this logic at all.

def _fake_response(json_body: dict) -> httpx.Response:
    request = httpx.Request("POST", "https://example.test")
    resp = httpx.Response(200, request=request, json=json_body)
    return resp


async def test_groq_retries_once_on_finish_reason_length():
    truncated = _fake_response({"choices": [{"message": {"content": "cut off mid"}, "finish_reason": "length"}]})
    complete_reply = _fake_response({"choices": [{"message": {"content": "a full sentence now."}, "finish_reason": "stop"}]})
    post_mock = AsyncMock(side_effect=[truncated, complete_reply])
    with patch.object(httpx.AsyncClient, "post", new=post_mock):
        provider = GroqProvider()
        result = await provider.call("some-model", "sys", "prompt", max_tokens=100)

    assert result == "a full sentence now."
    assert post_mock.call_count == 2
    # the retry used a larger budget, not the same one that just failed
    second_call_body = post_mock.call_args_list[1].kwargs["json"]
    assert second_call_body["max_tokens"] > 100


async def test_groq_does_not_retry_when_finish_reason_is_stop():
    complete_reply = _fake_response({"choices": [{"message": {"content": "done."}, "finish_reason": "stop"}]})
    post_mock = AsyncMock(return_value=complete_reply)
    with patch.object(httpx.AsyncClient, "post", new=post_mock):
        provider = GroqProvider()
        result = await provider.call("some-model", "sys", "prompt", max_tokens=100)

    assert result == "done."
    assert post_mock.call_count == 1  # no wasted retry when nothing was truncated


async def test_groq_keeps_original_content_if_retry_also_empty():
    truncated = _fake_response({"choices": [{"message": {"content": "cut off"}, "finish_reason": "length"}]})
    empty_retry = _fake_response({"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
    post_mock = AsyncMock(side_effect=[truncated, empty_retry])
    with patch.object(httpx.AsyncClient, "post", new=post_mock):
        provider = GroqProvider()
        result = await provider.call("some-model", "sys", "prompt", max_tokens=100)

    assert result == "cut off"  # never worse than what it already had


async def test_openrouter_retries_once_on_finish_reason_length():
    truncated = _fake_response({"choices": [{"message": {"content": "FINAL:\ncut off mid"}, "finish_reason": "length"}]})
    complete_reply = _fake_response({"choices": [{"message": {"content": "FINAL:\na full answer."}, "finish_reason": "stop"}]})
    post_mock = AsyncMock(side_effect=[truncated, complete_reply])
    with patch.object(httpx.AsyncClient, "post", new=post_mock):
        provider = OpenRouterProvider()
        result = await provider.call("some-model", "sys", "prompt", max_tokens=100)

    assert result == "a full answer."
    assert post_mock.call_count == 2


def _fake_gemini_response(text: str) -> httpx.Response:
    request = httpx.Request("POST", "https://example.test")
    body = {"steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}]}
    return httpx.Response(200, request=request, json=body)


async def test_gemini_retries_when_output_does_not_end_in_terminal_punctuation():
    # No confirmed finish-reason field for this endpoint (see the
    # provider's own fix comment) — heuristic instead: text not ending
    # in sentence-terminal punctuation looks truncated.
    truncated = _fake_gemini_response("Break this into three 50-minute study")
    complete_reply = _fake_gemini_response("Break this into three 50-minute study blocks.")
    post_mock = AsyncMock(side_effect=[truncated, complete_reply])
    with patch.object(httpx.AsyncClient, "post", new=post_mock):
        provider = GeminiProvider()
        result = await provider.call("some-model", "sys", "prompt", max_tokens=100)

    assert result == "Break this into three 50-minute study blocks."
    assert post_mock.call_count == 2


async def test_gemini_does_not_retry_when_output_ends_cleanly():
    complete_reply = _fake_gemini_response("This is a complete sentence.")
    post_mock = AsyncMock(return_value=complete_reply)
    with patch.object(httpx.AsyncClient, "post", new=post_mock):
        provider = GeminiProvider()
        result = await provider.call("some-model", "sys", "prompt", max_tokens=100)

    assert result == "This is a complete sentence."
    assert post_mock.call_count == 1


async def test_truncation_retry_is_capped_and_does_not_retry_past_ceiling():
    truncated = _fake_response({"choices": [{"message": {"content": "still cut off"}, "finish_reason": "length"}]})
    post_mock = AsyncMock(return_value=truncated)
    with patch.object(httpx.AsyncClient, "post", new=post_mock):
        provider = GroqProvider()
        # already at/above the ceiling — must not attempt a retry at all
        result = await provider.call("some-model", "sys", "prompt", max_tokens=5000)

    assert result == "still cut off"
    assert post_mock.call_count == 1
